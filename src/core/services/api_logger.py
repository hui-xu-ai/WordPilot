# api_logger.py
"""API 调用审计日志：每次请求一条 JSONL，供监控面板与费用统计使用。

字段（向后兼容旧日志：缺 `plugin`/`task` 时按 action 推断）：
    time / action / plugin / task / input / output / tokens / success / latency_ms
`tokens` 统一为 `{prompt, completion, total, cache_hit, cache_miss}`，
其中 `cache_hit` 来自 DeepSeek 的 `prompt_cache_hit_tokens`，用来判断提示词前缀是否命中缓存。
"""
import json
import os
import threading
from datetime import datetime

from core.services.config_manager import CONFIG_DIR  # 配置文件路径

LOG_FILE = CONFIG_DIR / "api_calls.log"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
_write_lock = threading.Lock()

#: action → (插件 id, 任务名)，给老日志补字段用
ACTION_OWNER = {
    "proof": ("proofread", "逐句校对"),
    "correct": ("clipboard", "整段纠错"),
    "translate": ("clipboard", "翻译"),
    "rewrite": ("clipboard", "改写"),
}


def _normalize_tokens(tokens):
    """将 DeepSeek 返回的 usage 或自定义字典统一为固定结构。"""
    if not tokens:
        return {"prompt": 0, "completion": 0, "total": 0, "cache_hit": 0, "cache_miss": 0}
    prompt = tokens.get("prompt_tokens", tokens.get("prompt", 0))
    completion = tokens.get("completion_tokens", tokens.get("completion", 0))
    total = tokens.get("total_tokens", tokens.get("total", 0))
    cache_hit = tokens.get("prompt_cache_hit_tokens", tokens.get("cache_hit", 0))
    cache_miss = tokens.get("prompt_cache_miss_tokens", tokens.get("cache_miss", 0))
    if total == 0 and (prompt > 0 or completion > 0):
        total = prompt + completion
    if prompt == 0 and (cache_hit or cache_miss):
        prompt = cache_hit + cache_miss
    return {"prompt": prompt, "completion": completion, "total": total,
            "cache_hit": cache_hit, "cache_miss": cache_miss}


def log_api_call(action, input_text, output_text, tokens, success, latency_ms=None,
                 plugin=None, task=None):
    """追加一条调用记录。

    `latency_ms`：本次请求的真实往返耗时（ms），用于判断"慢在网络还是慢在生成"；
    `plugin` / `task`：哪个插件的哪个任务（缺省按 `action` 推断）。
    """
    owner_plugin, owner_task = ACTION_OWNER.get(action, ("", action))
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d | %H:%M:%S"),
        "action": action,
        "plugin": plugin or owner_plugin,
        "task": task or owner_task,
        "input": input_text[:200] if input_text else "",
        "output": output_text[:200] if output_text else "",
        "tokens": _normalize_tokens(tokens),
        "success": success,
    }
    if latency_ms is not None:
        entry["latency_ms"] = int(latency_ms)
    try:
        with _write_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - 日志失败不影响主流程
        print(f"API日志写入失败: {e}")


def _normalise_entry(entry: dict) -> dict:
    action = entry.get("action", "")
    owner_plugin, owner_task = ACTION_OWNER.get(action, ("", action))
    entry.setdefault("plugin", owner_plugin)
    entry.setdefault("task", owner_task)
    entry["tokens"] = _normalize_tokens(entry.get("tokens"))
    entry["day"] = (entry.get("time") or "")[:10]
    entry["month"] = entry["day"][:7]
    return entry


def get_all_logs():
    """读出全部记录（旧记录补齐 plugin/task/tokens/day/month 字段）。"""
    if not os.path.exists(LOG_FILE):
        return []
    logs = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    logs.append(_normalise_entry(json.loads(line)))
                except json.JSONDecodeError:
                    continue
    except Exception as e:  # noqa: BLE001
        print(f"读取全部日志失败: {e}")
    return logs


def get_last_log():
    logs = get_all_logs()
    return logs[-1] if logs else None


# ---------------- 统计 ----------------
def _blank() -> dict:
    return {"count": 0, "prompt": 0, "cache_hit": 0, "completion": 0, "total": 0}


def _accumulate(bucket: dict, entry: dict) -> None:
    tokens = entry["tokens"]
    bucket["count"] += 1
    bucket["prompt"] += tokens["prompt"]
    bucket["cache_hit"] += tokens["cache_hit"]
    bucket["completion"] += tokens["completion"]
    bucket["total"] += tokens["total"]


def task_summary(logs=None) -> list[dict]:
    """按「插件 + 任务」统计：次数、输入、命中、输出、合计。"""
    rows: dict[tuple[str, str], dict] = {}
    for entry in (logs if logs is not None else get_all_logs()):
        key = (entry.get("plugin") or "-", entry.get("task") or entry.get("action") or "-")
        bucket = rows.setdefault(key, {"plugin": key[0], "task": key[1], **_blank()})
        _accumulate(bucket, entry)
    return sorted(rows.values(), key=lambda row: row["total"], reverse=True)


def period_summary(bucket_key: str, logs=None) -> list[dict]:
    """按 `day` 或 `month` 分组统计（日 / 月消耗记录）。"""
    rows: dict[str, dict] = {}
    for entry in (logs if logs is not None else get_all_logs()):
        label = entry.get(bucket_key) or "-"
        bucket = rows.setdefault(label, {bucket_key: label, **_blank()})
        _accumulate(bucket, entry)
    return sorted(rows.values(), key=lambda row: row[bucket_key], reverse=True)


def totals(prefix: str | None = None, logs=None) -> dict:
    """合计；`prefix` 传 `YYYY-MM` 或 `YYYY-MM-DD` 时只算该时段。"""
    result = _blank()
    for entry in (logs if logs is not None else get_all_logs()):
        if prefix and not (entry.get("day") or "").startswith(prefix):
            continue
        _accumulate(result, entry)
    return result


# 校对缓存已迁移至 core/services/proof_cache.py（SQLite），
# 本模块只保留「日志」单一职责；清空日志不再影响缓存命中。

def clear_api_log():
    """清空 API 调用日志文件。"""
    try:
        with _write_lock:
            if LOG_FILE.exists():
                LOG_FILE.unlink()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"清空日志失败: {e}")
        return False
