"""Low-level PDF content-stream parsing and vector watermark filtering."""

import re

import pymupdf as fitz

def _tokenize_content_stream(text: str):
    """返回 (kind, value, start, end) 元组；num 的 value 为浮点值。"""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \r\n\t":
            i += 1
            continue
        if ch == "<":
            if text.startswith("<<", i):
                j = text.find(">>", i)
                if j < 0:
                    j = i
                tokens.append(("other", text[i:j + 2], i, j + 2))
                i = j + 2
                continue
            j = text.find(">", i)
            if j < 0:
                j = i
            tokens.append(("other", text[i:j + 1], i, j + 1))
            i = j + 1
            continue
        if ch == "[":
            depth = 0
            j = i
            while j < n:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth <= 0:
                        j += 1
                        break
                j += 1
            tokens.append(("other", text[i:j], i, j))
            i = j
            continue
        if ch == "(":
            # 处理转义，找到真正的字符串结束括号
            j = i + 1
            depth = 1
            while j < n and depth > 0:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            tokens.append(("other", text[i:j], i, j))
            i = j
            continue
        if ch == "/":
            j = i + 1
            while j < n and text[j] not in " \r\n\t<>[]()":
                j += 1
            tokens.append(("name", text[i:j], i, j))
            i = j
            continue
        num_match = re.match(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text[i:])
        if num_match:
            raw = num_match.group()
            tokens.append(("num", float(raw), i, i + num_match.end()))
            i += num_match.end()
            continue
        op_match = re.match(r"[A-Za-z*]+", text[i:])
        if op_match:
            tokens.append(("op", op_match.group(), i, i + op_match.end()))
            i += op_match.end()
            continue
        tokens.append(("other", text[i], i, i + 1))
        i += 1
    return tokens


def _matrix_mul(A, B):
    a1, b1, c1, d1, e1, f1 = A
    a2, b2, c2, d2, e2, f2 = B
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _matrix_apply(pt, m):
    x, y = pt
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _path_bbox(path_ops, ctm):
    xs = []
    ys = []
    for op, coords in path_ops:
        for idx in range(0, len(coords), 2):
            x, y = _matrix_apply((coords[idx], coords[idx + 1]), ctm)
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _resolve_gs_alpha(doc, page, gs_name: str) -> float:
    try:
        res = doc.xref_get_key(page.xref, "Resources")
        res_text = res[1] if res[0] == "dict" else ""
        ext_match = re.search(r"/ExtGState\s*<<(.*?)>>", res_text, re.DOTALL)
        if not ext_match:
            return 1.0
        ref_match = re.search(r"%s\s+(\d+)\s+0\s+R" % re.escape(gs_name), ext_match.group(1))
        if not ref_match:
            return 1.0
        gs_obj = doc.xref_object(int(ref_match.group(1)))
        for key in ("/ca", "/CA"):
            m = re.search(key + r"\s+([\d.]+)", gs_obj)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return 1.0


def _filter_watermark_paths(doc, page, content_xref: int, target_rects) -> int:
    """从页面内容流中删除落在目标矩形内、且为浅色或半透明的填充路径。"""
    try:
        raw = doc.xref_stream(content_xref)
    except Exception:
        return 0
    text = raw.decode("latin1", "ignore")
    tokens = _tokenize_content_stream(text)
    page_height = page.rect.height
    state = {"ctm": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), "fill": None, "alpha": 1.0}
    state_stack = []
    path_ops = []
    pending = []
    out = []
    drop_ranges = []
    removed = 0
    last_name = None

    def pop_nums(count):
        if len(pending) < count:
            return None
        nums = pending[-count:]
        del pending[-count:]
        return nums

    for tok in tokens:
        kind = tok[0]
        if kind == "num":
            pending.append(tok[1])
            out.append(tok)
            continue
        if kind == "name":
            last_name = tok[1]
            out.append(tok)
            continue
        if kind != "op":
            out.append(tok)
            continue
        op = tok[1]

        if op == "q":
            state_stack.append(dict(state))
            out.append(tok)
        elif op == "Q":
            if state_stack:
                state = state_stack.pop()
            out.append(tok)
        elif op == "cm":
            nums = pop_nums(6)
            if nums:
                state["ctm"] = _matrix_mul(state["ctm"], (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5]))
            out.append(tok)
        elif op == "g":
            nums = pop_nums(1)
            if nums:
                state["fill"] = ("gray", nums[0])
            out.append(tok)
        elif op == "rg":
            nums = pop_nums(3)
            if nums:
                state["fill"] = ("rgb", (nums[0], nums[1], nums[2]))
            out.append(tok)
        elif op == "k":
            nums = pop_nums(4)
            if nums:
                state["fill"] = ("cmyk", (nums[0], nums[1], nums[2], nums[3]))
            out.append(tok)
        elif op in ("scn", "SCN"):
            # 数值着色（RGB/CMYK）记录颜色；带 Pattern 名称时按未知处理
            if len(pending) >= 3:
                nums = pending[-3:]
                del pending[-3:]
                state["fill"] = ("rgb", (nums[0], nums[1], nums[2]))
            elif len(pending) >= 4:
                nums = pending[-4:]
                del pending[-4:]
                state["fill"] = ("cmyk", (nums[0], nums[1], nums[2], nums[3]))
            out.append(tok)
        elif op == "gs":
            if last_name:
                state["alpha"] = _resolve_gs_alpha(doc, page, last_name)
            last_name = None
            out.append(tok)
        elif op in ("m", "l"):
            nums = pop_nums(2)
            if nums:
                path_ops.append((op, nums))
            out.append(tok)
        elif op == "re":
            nums = pop_nums(4)
            if nums:
                path_ops.append((op, nums))
            out.append(tok)
        elif op in ("c", "v", "y"):
            nums = pop_nums(6)
            if nums:
                path_ops.append((op, nums))
            out.append(tok)
        elif op == "h":
            path_ops.append((op, []))
            out.append(tok)
        elif op in ("f", "f*", "B", "B*", "b", "b*", "s", "S", "n"):
            bb = _path_bbox(path_ops, state["ctm"])
            lum = None
            if state["fill"]:
                ft, fv = state["fill"]
                if ft == "gray":
                    lum = fv
                elif ft == "rgb":
                    lum = 0.2126 * fv[0] + 0.7152 * fv[1] + 0.0722 * fv[2]
                elif ft == "cmyk":
                    lum = 1.0 - min(1.0, fv[0] + fv[1] + fv[2] + fv[3])
            pale = lum is not None and lum >= 0.5
            translucent = state["alpha"] < 0.9
            drop = False
            if bb and op in ("f", "f*", "B", "B*", "b", "b*") and (pale or translucent):
                bb_top = (bb[0], page_height - bb[3], bb[2], page_height - bb[1])
                for tr in target_rects:
                    if not (bb_top[2] < tr.x0 or bb_top[0] > tr.x1 or bb_top[3] < tr.y0 or bb_top[1] > tr.y1):
                        drop = True
                        break
            if drop:
                remove_count = 0
                for po, pnums in path_ops:
                    remove_count += 1 + len(pnums)
                dropped = out[-remove_count:]
                del out[-remove_count:]
                if dropped:
                    drop_ranges.append((dropped[0][2], dropped[-1][3]))
                removed += 1
            else:
                out.append(tok)
            path_ops = []
        else:
            out.append(tok)

    if removed and drop_ranges:
        drop_ranges.sort()
        merged = []
        for start, end in drop_ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        rebuilt = []
        cursor = 0
        for start, end in merged:
            rebuilt.append(text[cursor:start])
            cursor = end
        rebuilt.append(text[cursor:])
        doc.update_stream(content_xref, "".join(rebuilt).encode("latin1", "ignore"))
    return removed
