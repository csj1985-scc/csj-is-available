"""Debug 工具箱 — 从 agent.py 拆分出来"""
import json as _json


class _DebugToolkit:
    def check_function(self, func, test_cases):
        results = []
        for args, expected in test_cases:
            try:
                got = func(*args) if isinstance(args, tuple) else func(args)
                ok = got == expected
                results.append({"args": args, "expected": expected, "got": got, "pass": ok, "error": None})
            except Exception as e:
                results.append({"args": args, "expected": expected, "got": None, "pass": False, "error": f"{type(e).__name__}: {e}"})
        passed = sum(1 for r in results if r["pass"])
        failed = sum(1 for r in results if not r["pass"])
        summary_parts = [f"通过 {passed}/{len(results)}"]
        if failed:
            first_fail = next(r for r in results if not r["pass"])
            summary_parts.append(f"首个失败: args={first_fail['args']!r}")
            if first_fail.get("error"):
                summary_parts.append(f"错误: {first_fail['error']}")
            else:
                summary_parts.append(f"期望 {first_fail['expected']!r} 得到 {first_fail['got']!r}")
        return {"passed": passed, "failed": failed, "total": len(results), "results": results, "summary": " | ".join(summary_parts)}

    def check_boundaries(self, func):
        alerts = []
        tested = 0
        boundary_inputs = [([], "空列表"), (None, "None"), ([None], "含None的列表"), (0, "零"), (float('inf'), "无穷大")]
        for val, label in boundary_inputs:
            try:
                func(val)
            except Exception as e:
                alerts.append({"input": label, "value": repr(val)[:80], "error": f"{type(e).__name__}: {e}"})
            tested += 1
        return {"tested": tested, "alerts": alerts, "summary": f"边界测试 {tested} 项，{len(alerts)} 个触发异常"}

    def check_var_consistency(self, source_code):
        import re, itertools
        issues = []
        identifiers = set(re.findall(r'\b[a-zA-Z_]\w*\b', source_code))
        keywords = {'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield', 'print', 'len', 'range', 'type', 'int', 'str', 'float', 'list', 'dict', 'set', 'tuple', 'bool', 'isinstance', 'hasattr', 'getattr', 'setattr', 'repr', 'max', 'min', 'sum', 'abs', 'any', 'all', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'open', 'input', 'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError', 'StopIteration'}
        identifiers -= keywords
        id_list = sorted(identifiers)
        for a, b in itertools.combinations(id_list, 2):
            if len(a) < 3 or len(b) < 3: continue
            if abs(len(a) - len(b)) <= 2:
                common = sum(1 for x, y in zip(a, b) if x == y)
                if common >= max(len(a), len(b)) - 2 and a != b:
                    issues.append({"type": "similar_names", "names": [a, b], "detail": f"变量名 '{a}' 和 '{b}' 相似，可能为拼写错误"})
        return {"issues": issues, "summary": f"检查 {len(identifiers)} 个变量，发现 {len(issues)} 个潜在问题"}

    def check_return_type(self, func, expected_type, test_input=None):
        try:
            args = (test_input,) if test_input is not None else ()
            result = func(*args) if args else func()
            got = type(result)
            match = got == expected_type
            return {"match": match, "expected_type": expected_type.__name__, "got_type": got.__name__, "value_preview": repr(result)[:100], "summary": "类型匹配" if match else f"期望 {expected_type.__name__} 得到 {got.__name__}"}
        except Exception as e:
            return {"match": False, "expected_type": expected_type.__name__, "got_type": "error", "error": f"{type(e).__name__}: {e}", "summary": f"调用时异常: {e}"}

    def merge_messages(self, messages, max_tokens=4000):
        if not messages: return []
        merged = []
        buffer = ""
        for msg in messages:
            if not msg: continue
            if len(buffer) + len(msg) + 1 <= max_tokens:
                buffer = (buffer + "\n" + msg).strip()
            else:
                if buffer: merged.append(buffer)
                if len(msg) > max_tokens:
                    merged.append(msg[:max_tokens])
                    buffer = ""
                else:
                    buffer = msg
        if buffer: merged.append(buffer)
        return merged

    def min_run_length(self, nums, min_val=3):
        if not nums: return 0
        min_len = float('inf')
        current_run = 0
        found = False
        for n in nums:
            if n >= min_val:
                current_run += 1
            else:
                if current_run > 0:
                    min_len = min(min_len, current_run)
                    found = True
                current_run = 0
        if current_run > 0:
            min_len = min(min_len, current_run)
            found = True
        return int(min_len) if found else 0


debug = _DebugToolkit()


def run_debug_check(args: dict) -> dict:
    action = args.get("action", "")
    params = args.get("params", "{}")
    if isinstance(params, str):
        params = _json.loads(params)

    if action == "debug.check_function":
        func_name = params.get("func_name", "")
        test_cases = params.get("test_cases", [])
        converted = []
        for case in test_cases:
            c_args = case[0] if len(case) > 0 else ()
            expected = case[1] if len(case) > 1 else None
            converted.append((tuple(c_args) if isinstance(c_args, list) else c_args, expected))
        def _fake(*a):
            return f"需替换为实际函数 {func_name}({a})"
        r = debug.check_function(_fake, converted)
        return {"success": True, "result": r, "summary": r["summary"]}

    elif action == "debug.check_boundaries":
        r = debug.check_boundaries(lambda x: x)
        return {"success": True, "result": r, "summary": r["summary"]}

    elif action == "debug.check_var_consistency":
        code = params.get("source_code", "")
        if not code:
            return {"error": "需要 source_code 参数"}
        r = debug.check_var_consistency(code)
        return {"success": True, "result": r, "summary": r["summary"]}

    elif action == "debug.check_return_type":
        r = debug.check_return_type(lambda x: x, str, "test")
        return {"success": True, "result": r, "summary": r["summary"]}

    elif action == "debug.merge_messages":
        messages = params.get("messages", [])
        max_tokens = params.get("max_tokens", 4000)
        r = debug.merge_messages(messages, max_tokens)
        return {"success": True, "result": r, "summary": f"合并为 {len(r)} 条消息"}

    elif action == "debug.min_run_length":
        nums = params.get("nums", [])
        min_val = params.get("min_val", 3)
        r = debug.min_run_length(nums, min_val)
        return {"success": True, "result": r, "summary": f"最小运行长度: {r}"}

    else:
        return {"error": f"未知 debug 操作: {action}"}
