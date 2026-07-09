import argparse
import datetime as dt
import json
import os
import re
import time
from typing import Dict, Optional, Set, Tuple

import requests


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5-mini"

FREE_TIER_250K_MODELS = {
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5.1-codex",
    "gpt-5",
    "gpt-5-codex",
    "gpt-5-chat-latest",
    "gpt-4.1",
    "gpt-4o",
    "o1",
    "o3",
}
FREE_TIER_2_5M_MODELS = {
    "gpt-5.1-codex-mini",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o-mini",
    "o1-mini",
    "o3-mini",
    "o4-mini",
    "codex-mini-latest",
}


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def env_optional_float(name: str) -> Optional[float]:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def today_iso_local() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


def default_daily_limit_for_model(model: str) -> int:
    m = (model or "").strip()
    if m in FREE_TIER_250K_MODELS:
        return 250_000
    if m in FREE_TIER_2_5M_MODELS:
        return 2_500_000
    return 0


def estimate_prompt_tokens(text: str) -> int:
    # Conservative heuristic for pre-call guard.
    n_chars = len(text or "")
    return max(1, (n_chars + 2) // 3)


def extract_usage_tokens(data: Dict) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None, None

    in_t = usage.get("input_tokens")
    out_t = usage.get("output_tokens")
    total_t = usage.get("total_tokens")

    def _to_int(v) -> Optional[int]:
        try:
            if v is None:
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    return _to_int(in_t), _to_int(out_t), _to_int(total_t)


def load_budget_state(path: str, today: str) -> Dict[str, int]:
    state = {
        "date": today,
        "tokens_used": 0,
        "input_tokens_used": 0,
        "output_tokens_used": 0,
    }
    if not path or not os.path.exists(path):
        return state

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = json.load(f)
    except Exception:
        return state

    if not isinstance(raw, dict):
        return state

    if str(raw.get("date", "")) != today:
        return state

    for k in ("tokens_used", "input_tokens_used", "output_tokens_used"):
        try:
            state[k] = max(0, int(raw.get(k, 0)))
        except (TypeError, ValueError):
            state[k] = 0
    return state


def save_budget_state(path: str, state: Dict[str, int], model: str) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "date": state.get("date", ""),
        "tokens_used": int(state.get("tokens_used", 0)),
        "input_tokens_used": int(state.get("input_tokens_used", 0)),
        "output_tokens_used": int(state.get("output_tokens_used", 0)),
        "model": model,
        "updated_at_unix": int(time.time()),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def extract_output_text(data: Dict) -> str:
    v = data.get("output_text")
    if isinstance(v, str) and v.strip():
        return v.strip()

    output = data.get("output")
    if isinstance(output, list):
        chunks = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    txt = c.get("text")
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt)
        if chunks:
            return "\n".join(chunks).strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    chunks = []
                    for c in content:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            chunks.append(c["text"])
                    if chunks:
                        return "\n".join(chunks).strip()
    return ""


def extract_json_from_text(text: str) -> Tuple[Optional[Dict], str]:
    raw = (text or "").strip()
    if not raw:
        return None, "empty_output"

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj, ""
        return None, "json_not_object"
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, ""
            return None, "json_not_object"
        except json.JSONDecodeError:
            pass

    i = raw.find("{")
    j = raw.rfind("}")
    if i >= 0 and j > i:
        candidate = raw[i : j + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, ""
            return None, "json_not_object"
        except json.JSONDecodeError:
            return None, "invalid_json_object"

    return None, "no_json_object_found"


def load_done_pubs(path: str, only_status_ok: bool = True) -> Set[str]:
    done: Set[str] = set()
    if not os.path.exists(path):
        return done

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                row = json.loads(s)
            except json.JSONDecodeError:
                continue
            if only_status_ok:
                status = row.get("status")
                if status != "ok":
                    continue
            pub = row.get("pub")
            if isinstance(pub, str) and pub:
                done.add(pub)
    return done


def call_openai_responses(
    session: requests.Session,
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_s: float,
    max_retries: int,
    temperature: Optional[float],
    max_output_tokens: int,
    org_id: str,
    project_id: str,
) -> Tuple[bool, Dict]:
    url = base_url.rstrip("/") + "/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if org_id:
        headers["OpenAI-Organization"] = org_id
    if project_id:
        headers["OpenAI-Project"] = project_id

    payload = {
        "model": model,
        "input": prompt,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if int(max_output_tokens) > 0:
        payload["max_output_tokens"] = int(max_output_tokens)

    tries = max(1, max_retries)
    for attempt in range(1, tries + 1):
        try:
            r = session.post(url, headers=headers, json=payload, timeout=timeout_s)
        except requests.RequestException as e:
            if attempt == tries:
                return False, {"error": f"request_error:{type(e).__name__}"}
            time.sleep(min(2 ** (attempt - 1), 20))
            continue

        if r.status_code in (408, 429, 500, 502, 503, 504):
            if attempt == tries:
                body = ""
                try:
                    body = r.text[:400]
                except Exception:
                    pass
                return False, {"error": f"http_{r.status_code}", "body": body}
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_s = float(retry_after)
                except ValueError:
                    wait_s = float(min(2 ** (attempt - 1), 20))
            else:
                wait_s = float(min(2 ** (attempt - 1), 20))
            time.sleep(wait_s)
            continue

        if r.status_code >= 400:
            body = ""
            try:
                body = r.text[:800]
            except Exception:
                pass
            return False, {"error": f"http_{r.status_code}", "body": body}

        try:
            data = r.json()
        except ValueError:
            return False, {"error": "invalid_json_response", "body": (r.text or "")[:800]}

        return True, data

    return False, {"error": "unexpected_exit"}


def wait_for_input_file(path: str, follow: bool, poll_s: float) -> None:
    if os.path.exists(path):
        return
    if not follow:
        raise FileNotFoundError(path)
    while not os.path.exists(path):
        time.sleep(max(0.1, poll_s))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Read prompt JSONL and call OpenAI for each llm_prompt; write parsed results to JSONL."
    )
    ap.add_argument("--in-jsonl", required=True)
    ap.add_argument("--out-jsonl", required=True)
    ap.add_argument("--follow", action="store_true", help="Keep reading as input JSONL grows")
    ap.add_argument(
        "--idle-timeout",
        type=float,
        default=0.0,
        help="In follow mode: stop after N seconds with no new input. 0 means never stop.",
    )
    ap.add_argument("--poll-interval", type=float, default=1.0)
    ap.add_argument("--max-records", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="Skip pubs already present in out-jsonl")
    ap.add_argument(
        "--resume-any-status",
        action="store_true",
        help="With --resume, also skip rows that previously failed. Default is to skip only status=ok.",
    )
    ap.add_argument("--keep-raw-response", action="store_true")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    ap.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument(
        "--temperature",
        type=float,
        default=env_optional_float("OPENAI_TEMPERATURE"),
        help="Optional; when unset, temperature is omitted from API payload.",
    )
    ap.add_argument("--max-output-tokens", type=int, default=env_int("OPENAI_MAX_OUTPUT_TOKENS", 3000))
    ap.add_argument("--timeout", type=float, default=env_float("OPENAI_TIMEOUT_S", 120.0))
    ap.add_argument("--max-retries", type=int, default=env_int("OPENAI_MAX_RETRIES", 6))
    ap.add_argument(
        "--daily-token-limit",
        type=int,
        default=env_int("OPENAI_DAILY_TOKEN_LIMIT", 0),
        help="Hard daily token cap. 0 means auto free-tier cap by model unless disabled.",
    )
    ap.add_argument(
        "--usage-state",
        default=os.getenv("OPENAI_USAGE_STATE_PATH", ""),
        help="Path for persisted daily token usage ledger.",
    )
    ap.add_argument(
        "--disable-free-tier-guard",
        action="store_true",
        help="Disable automatic free-tier budget guard.",
    )
    args = ap.parse_args()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing env OPENAI_API_KEY")

    org_id = os.getenv("OPENAI_ORG_ID", "").strip()
    project_id = os.getenv("OPENAI_PROJECT_ID", "").strip()

    os.makedirs(os.path.dirname(args.out_jsonl) or ".", exist_ok=True)

    usage_state_path = args.usage_state or (args.out_jsonl + ".usage_state.json")
    today = today_iso_local()

    daily_limit = int(args.daily_token_limit)
    if daily_limit <= 0 and not args.disable_free_tier_guard:
        daily_limit = default_daily_limit_for_model(args.model)
        if daily_limit <= 0:
            raise SystemExit(
                f"Model '{args.model}' is not in configured free-tier map. "
                "Set --daily-token-limit explicitly, or pass --disable-free-tier-guard."
            )

    budget_state = load_budget_state(usage_state_path, today)

    done: Set[str] = set()
    if args.resume:
        done = load_done_pubs(args.out_jsonl, only_status_ok=not args.resume_any_status)

    wait_for_input_file(args.in_jsonl, args.follow, args.poll_interval)

    session = requests.Session()

    processed = 0
    written = 0
    skipped_no_prompt = 0
    skipped_done = 0
    api_errors = 0
    parse_errors = 0
    budget_stops = 0

    with open(args.in_jsonl, "r", encoding="utf-8", errors="replace") as inp, open(
        args.out_jsonl, "a", encoding="utf-8"
    ) as out:
        pending = ""
        last_data_ts = time.time()
        line_no = 0

        while True:
            chunk = inp.readline()
            if chunk == "":
                if not args.follow:
                    break
                if args.idle_timeout > 0 and (time.time() - last_data_ts) >= args.idle_timeout:
                    break
                time.sleep(max(0.1, args.poll_interval))
                continue

            last_data_ts = time.time()
            pending += chunk
            if not pending.endswith("\n"):
                continue

            raw_line = pending.strip()
            pending = ""
            if not raw_line:
                continue

            line_no += 1
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue

            pub = row.get("pub")
            if not isinstance(pub, str) or not pub:
                pub = f"line:{line_no}"

            if pub in done:
                skipped_done += 1
                continue

            prompt = row.get("llm_prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                skipped_no_prompt += 1
                continue

            est_input_tokens = estimate_prompt_tokens(prompt)
            est_total_tokens = est_input_tokens + max(0, int(args.max_output_tokens))

            if daily_limit > 0:
                projected = int(budget_state.get("tokens_used", 0)) + est_total_tokens
                if projected > daily_limit:
                    budget_stops += 1
                    print(
                        "Budget stop: projected tokens would exceed daily limit "
                        f"({projected} > {daily_limit}) for pub={pub}. "
                        f"Used so far={budget_state.get('tokens_used', 0)}."
                    )
                    save_budget_state(usage_state_path, budget_state, args.model)
                    break

            ok, payload = call_openai_responses(
                session,
                base_url=args.base_url,
                api_key=api_key,
                model=args.model,
                prompt=prompt,
                timeout_s=args.timeout,
                max_retries=args.max_retries,
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
                org_id=org_id,
                project_id=project_id,
            )
            processed += 1

            result: Dict[str, object] = {
                "pub": pub,
                "model": args.model,
                "source_pdf_url": row.get("pdf_url"),
                "source_title": row.get("title"),
                "source_github_mentions": row.get("github_mentions"),
                "status": "",
                "response_json": None,
                "parse_error": "",
                "api_error": "",
                "api_error_body": "",
                "usage_input_tokens": None,
                "usage_output_tokens": None,
                "usage_total_tokens": None,
                "usage_estimated_total_tokens": est_total_tokens,
                "budget_daily_limit": daily_limit if daily_limit > 0 else None,
                "budget_used_after": None,
            }

            if not ok:
                api_errors += 1
                result["status"] = "api_error"
                result["api_error"] = payload.get("error", "unknown_api_error")
                result["api_error_body"] = payload.get("body", "")
                if args.keep_raw_response:
                    result["raw_response"] = payload
            else:
                in_tok, out_tok, total_tok = extract_usage_tokens(payload)
                result["usage_input_tokens"] = in_tok
                result["usage_output_tokens"] = out_tok
                result["usage_total_tokens"] = total_tok

                consumed_total = total_tok if total_tok is not None else est_total_tokens
                consumed_input = in_tok if in_tok is not None else est_input_tokens
                consumed_output = out_tok if out_tok is not None else max(0, consumed_total - consumed_input)

                budget_state["tokens_used"] = int(budget_state.get("tokens_used", 0)) + int(consumed_total)
                budget_state["input_tokens_used"] = int(budget_state.get("input_tokens_used", 0)) + int(consumed_input)
                budget_state["output_tokens_used"] = int(budget_state.get("output_tokens_used", 0)) + int(consumed_output)
                result["budget_used_after"] = int(budget_state.get("tokens_used", 0))

                txt = extract_output_text(payload)
                obj, parse_err = extract_json_from_text(txt)
                if obj is None:
                    parse_errors += 1
                    result["status"] = "parse_error"
                    result["parse_error"] = parse_err
                    if args.keep_raw_response:
                        result["raw_response"] = payload
                    result["response_text"] = txt
                else:
                    result["status"] = "ok"
                    result["response_json"] = obj
                    if args.keep_raw_response:
                        result["raw_response"] = payload

            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
            written += 1
            done.add(pub)
            save_budget_state(usage_state_path, budget_state, args.model)

            if args.max_records > 0 and written >= args.max_records:
                break

    print(f"Input: {args.in_jsonl}")
    print(f"Output: {args.out_jsonl}")
    print(f"Processed API calls: {processed}")
    print(f"Written rows: {written}")
    print(f"Skipped (already done): {skipped_done}")
    print(f"Skipped (missing llm_prompt): {skipped_no_prompt}")
    print(f"API errors: {api_errors}")
    print(f"Parse errors: {parse_errors}")
    print(f"Budget stops: {budget_stops}")
    if daily_limit > 0:
        print(
            "Budget usage: "
            f"{budget_state.get('tokens_used', 0)} / {daily_limit} tokens "
            f"(input={budget_state.get('input_tokens_used', 0)}, "
            f"output={budget_state.get('output_tokens_used', 0)})"
        )
    print(f"Usage state: {usage_state_path}")


if __name__ == "__main__":
    main()
