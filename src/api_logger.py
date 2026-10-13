# api_logger.py
import json
import os
import threading  # 新增 --修改16
from datetime import datetime

from config_manager import CONFIG_DIR  # 配置文件路径

LOG_FILE = CONFIG_DIR / "api_calls.log"     #  配置文件路径
CONFIG_DIR.mkdir(parents=True, exist_ok=True)   #  确认 CONFIG_DIR 是否存在，没有新建
_write_lock = threading.Lock()           # 新增：写锁    --修改16

def _normalize_tokens(tokens):
    """将 DeepSeek 返回的 usage 或自定义字典统一为 {prompt, completion, total}"""
    if not tokens:
        return {"prompt": 0, "completion": 0, "total": 0}
    prompt = tokens.get("prompt_tokens", tokens.get("prompt", 0))
    completion = tokens.get("completion_tokens", tokens.get("completion", 0))
    total = tokens.get("total_tokens", tokens.get("total", 0))
    if total == 0 and (prompt > 0 or completion > 0):
        total = prompt + completion
    return {"prompt": prompt, "completion": completion, "total": total}

def log_api_call(action, input_text, output_text, tokens, success):
    entry = {
        # "time": datetime.now().isoformat(),
        "time": datetime.now().strftime("%Y-%m-%d | %H:%M:%S"),
        "action": action,
        "input": input_text[:200] if input_text else "",
        "output": output_text[:200] if output_text else "",
        "tokens": _normalize_tokens(tokens),
        "success": success
    }
    try:
        with _write_lock:   # 新增：确保同一时间只有一个线程写入     --修改16 
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"API日志写入失败: {e}")

def get_last_log():
    if not os.path.exists(LOG_FILE):       
        return None
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in reversed(lines):
            if line.strip():
                return json.loads(line)
    except Exception as e:
        print(f"读取日志失败: {e}")
    return None

def get_all_logs():
    logs = []
    if not os.path.exists(LOG_FILE):       
        return logs
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
    except Exception as e:
        print(f"读取全部日志失败: {e}")
    return logs


#  --修改16      历史建议查询     优先最新 50 条记录
def get_cached_suggestion(text: str, num: int = 50) -> str | None:
    """从最近 num 条日志中查找该文本的 proof 成功记录，返回输出；未找到则返回 None"""
    if not LOG_FILE.exists():
        return None
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-num:]
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if (entry.get("action") == "proof" and
                    entry.get("input") == text and
                    entry.get("success")):
                    return entry.get("output")
            except:
                continue
    except:
        pass
    return None   
    
def clear_api_log():                #   --修改18  
    """清空 API 调用日志文件"""
    if LOG_FILE.exists():
        LOG_FILE.unlink()    
    