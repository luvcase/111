"""Conservative, local extraction of regular square black/white grids.

This reads pixels, not puzzle rules. Bounds are inclusive in original-image
coordinates. Ambiguous centre patches stay None; no choice is selected here.
"""

import io
import json
import re
import time

from PIL import Image, ImageDraw, ImageStat

from .images import encode_question_image

TOP_EDGES = re.compile(b"\x01{24,800}")


def read_grids(data, time_limit=3.0):
    start = time.monotonic()
    with Image.open(io.BytesIO(data)) as source:
        gray = source.convert("L")
    width, height = gray.size
    pixels = gray.point(lambda p: int(p < 100)).tobytes()
    found = []
    examined = 0
    limited = False

    def horizontal(x, y, size):
        return sum(pixels[y * width + x : y * width + x + size]) / size

    def vertical(x, y, size):
        return sum(pixels[(y + dy) * width + x] for dy in range(size)) / size

    for y in range(height - 24):
        if time.monotonic() - start > time_limit or examined >= 4000:
            limited = True
            break
        row = pixels[y * width : (y + 1) * width]
        for match in TOP_EDGES.finditer(row):
            x, end = match.span()
            size = end - x
            if any(a <= x <= c and b <= y <= d for a, b, c, d in (g["bounds"] for g in found)):
                continue
            examined += 1
            for bottom in range(y + size - 3, min(y + size + 3, height)):
                if bottom <= y or horizontal(x, bottom, size) < 0.96:
                    continue
                if min(vertical(x, y, bottom - y + 1), vertical(end - 1, y, bottom - y + 1)) < 0.96:
                    continue
                candidates = []
                for count in range(2, 9):
                    cell = (size - 1) / count
                    if cell < 6:
                        continue
                    lines = []
                    for k in range(1, count):
                        xx = round(x + k * cell)
                        yy = round(y + k * (bottom - y) / count)
                        # Tolerate one pixel of border/rounding difference.
                        lines.extend(
                            [
                                max(
                                    vertical(xx + delta, y, bottom - y + 1) for delta in (-1, 0, 1)
                                ),
                                max(horizontal(x, yy + delta, size) for delta in (-1, 0, 1)),
                            ]
                        )
                    if min(lines) < 0.94:
                        continue
                    matrix, uncertain = sample_cells(gray, (x, y, end - 1, bottom), count)
                    values = [v for row_values in matrix for v in row_values]
                    # A solid block or a plain outlined box is not sufficient evidence.
                    if 0 not in values or 1 not in values:
                        continue
                    candidates.append((len(uncertain), count, matrix, uncertain))
                if not candidates:
                    continue
                candidates.sort(key=lambda item: item[0])
                best = candidates[0]
                if len(candidates) > 1 and candidates[1][0] == best[0]:
                    continue  # Grid dimensions are not distinguishable.
                found.append(
                    {
                        "bounds": [x, y, end - 1, bottom],
                        "size": best[1],
                        "matrix": best[2],
                        "uncertain": best[3],
                    }
                )
                break
        if len(found) >= 24:
            limited = True
            break
    # Reading order only: these identifiers do not imply question/option labels.
    found.sort(key=lambda grid: (grid["bounds"][1] // 12, grid["bounds"][0]))
    for i, grid in enumerate(found, 1):
        grid["id"] = f"G{i}"
    return {
        "version": 1,
        "image_size": [width, height],
        "grids": found,
        "limited": limited,
        "elapsed_ms": round((time.monotonic() - start) * 1000),
        "message": "只读规则黑白方格；编号按图片位置排列，不代表题号或选项。"
        "请对照检查图核验；没有读到的图形仍需查看原图。",
    }


def sample_cells(gray, bounds, count):
    x, y, right, bottom = bounds
    cw, ch = (right - x) / count, (bottom - y) / count
    matrix, uncertain = [], []
    for r in range(count):
        row = []
        for c in range(count):
            # Sample the middle 30%, keeping thin lines away from the patch.
            box = (
                round(x + (c + 0.35) * cw),
                round(y + (r + 0.35) * ch),
                round(x + (c + 0.65) * cw),
                round(y + (r + 0.65) * ch),
            )
            patch = gray.crop(box)
            mean = ImageStat.Stat(patch).mean[0]
            histogram = patch.histogram()
            total = patch.width * patch.height
            value = None
            if mean <= 70 and sum(histogram[:100]) / total >= 0.9:
                value = 1
            elif mean >= 185 and sum(histogram[155:]) / total >= 0.9:
                value = 0
            if value is None:
                uncertain.append([r + 1, c + 1])
            row.append(value)
        matrix.append(row)
    return matrix, uncertain


def annotate_grids(data, report):
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for grid in report["grids"]:
        x, y, right, bottom = grid["bounds"]
        draw.rectangle((x, y, right, bottom), outline="#007f75", width=2)
        draw.text((x, max(0, y - 12)), grid["id"], fill="#007f75")
        n = grid["size"]
        for r, row in enumerate(grid["matrix"]):
            for c, value in enumerate(row):
                xx = round(x + (c + 0.5) * (right - x) / n)
                yy = round(y + (r + 0.5) * (bottom - y) / n)
                color = "#ee6e00" if value is None else "#e00067"
                draw.ellipse((xx - 2, yy - 2, xx + 2, yy + 2), fill=color)
    return encode_question_image(image)


def grid_context(report):
    if not report["grids"]:
        return ""
    return (
        "\n本地像素读格的候选数据（并非人工确认）：1=黑、0=白、null=不确定。"
        "行从上到下、列从左到右，bounds 为原图像素边界，G 编号不是选项标签。"
        "结合原图确认编号对应关系；不要把不确定格填成确定格，不要仅凭黑格总数选答案。"
        "规律须同时解释全部已知图的位置变化，并与选项逐格比较。"
        "检测可能遗漏图形，不能把以下列表当成完整题目。\n"
        + json.dumps({"limited": report["limited"], "grids": report["grids"]}, ensure_ascii=False)
    )
