import io
import random

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from screenbridge.app import create_app
from screenbridge.grids import annotate_grids, grid_context, read_grids
from screenbridge.provider import Settings

TOKEN = "local-grid-testing-token-12345"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def picture(matrices, cell=18, line=1, scale=1):
    image = Image.new("RGB", (700, 450), "white")
    draw = ImageDraw.Draw(image)
    for index, matrix in enumerate(matrices):
        x, y = 27 + index * 130, 39
        n = len(matrix)
        for r, row in enumerate(matrix):
            for c, value in enumerate(row):
                fill = "#808080" if value is None else ("black" if value else "white")
                draw.rectangle(
                    (x + c * cell, y + r * cell, x + (c + 1) * cell, y + (r + 1) * cell), fill=fill
                )
        for k in range(n + 1):
            draw.line((x, y + k * cell, x + n * cell, y + k * cell), fill="black", width=line)
            draw.line((x + k * cell, y, x + k * cell, y + n * cell), fill="black", width=line)
    if scale != 1:
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    "count,cell,line,scale",
    [
        (2, 12, 1, 1),
        (3, 17, 2, 1),
        (4, 20, 1, 1),
        (4, 26, 3, 0.8),
        (5, 14, 1, 1),
        (6, 15, 2, 1),
        (7, 12, 1, 1),
        (8, 10, 1, 1),
    ],
)
def test_independent_patterns_are_extracted(count, cell, line, scale):
    rng = random.Random(31 + count)
    matrix = [[rng.randrange(2) for _ in range(count)] for _ in range(count)]
    result = read_grids(picture([matrix], cell, line, scale))
    assert not result["limited"]
    assert len(result["grids"]) == 1
    assert result["grids"][0]["matrix"] == matrix


def test_ambiguous_gray_cell_stays_unknown_and_is_annotated():
    matrix = [[1, 0, 1, 0], [0, None, 0, 1], [1, 0, 1, 0], [0, 1, 0, 1]]
    data = picture([matrix])
    result = read_grids(data)
    assert result["grids"][0]["matrix"] == matrix
    assert result["grids"][0]["uncertain"] == [[2, 2]]
    annotated = Image.open(io.BytesIO(annotate_grids(data, result)))
    assert annotated.size == (700, 450)
    assert annotated.getpixel((54, 66)) == (238, 110, 0)
    assert "null" in grid_context(result)


def test_multiple_grids_not_labeled_as_options():
    matrices = [[[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[0, 1, 0], [1, 0, 1], [0, 1, 0]]]
    result = read_grids(picture(matrices))
    assert [g["matrix"] for g in result["grids"]] == matrices
    assert [g["id"] for g in result["grids"]] == ["G1", "G2"]
    assert "并非人工确认" in grid_context(result)


def test_plain_boxes_and_non_grid_drawing_are_not_matrices():
    image = Image.new("RGB", (500, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 90, 90), outline="black")
    draw.rectangle((110, 10, 190, 90), fill="black")
    draw.ellipse((200, 30, 300, 130), outline="black", width=2)
    draw.text((20, 150), "NOT A GRID", fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    result = read_grids(output.getvalue())
    assert result["grids"] == []
    assert grid_context(result) == ""


def test_work_budget_is_reported():
    result = read_grids(picture([[[1, 0], [0, 1]]]), time_limit=0)
    assert result["limited"] and result["grids"] == []


def test_transcribed_example_in_question_and_vertical_option_layout():
    # Synthetic raster from a manual transcription; not the user's uploaded pixels.
    patterns = [
        "0111/0100/0010/0000",
        "1110/0010/0100/0000",
        "1100/1100/0010/0000",
        "1000/1010/1100/0000",
        "1100/1000/1010/0000",
        "0000/1100/1010/1000",
        "1111/0010/0000/0000",
        "0111/0100/0100/0000",
    ]
    matrices = [[[int(value) for value in row] for row in p.split("/")] for p in patterns]
    image = Image.new("RGB", (1000, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 8), "Choose the next diagram", fill="black")
    draw.rectangle((10, 24, 510, 132), outline="black", width=2)
    for index, matrix in enumerate(matrices):
        tile = Image.open(io.BytesIO(picture([matrix], cell=16))).crop((24, 36, 95, 107))
        position = (25 + index * 105, 38) if index < 4 else (30, 160 + (index - 4) * 125)
        image.paste(tile, position)
    output = io.BytesIO()
    image.save(output, "PNG")
    result = read_grids(output.getvalue())
    assert [g["matrix"] for g in result["grids"]] == matrices


def test_jpeg_artifacts_do_not_change_clear_cells():
    matrix = [[1, 0, 1, 0], [0, 1, 0, 1], [1, 0, 0, 1], [0, 1, 1, 0]]
    image = Image.open(io.BytesIO(picture([matrix], cell=20)))
    output = io.BytesIO()
    image.save(output, "JPEG", quality=65)
    assert read_grids(output.getvalue())["grids"][0]["matrix"] == matrix


def test_local_preview_needs_no_api_key_and_requires_auth(tmp_path):
    with TestClient(create_app(Settings(), TOKEN, data_dir=tmp_path)) as client:
        data = picture([[[1, 0, 0], [0, 1, 0], [0, 0, 1]]])
        assert client.post("/api/grids", files={"image": ("grid.png", data)}).status_code == 401
        response = client.post("/api/grids", headers=HEADERS, files={"image": ("grid.png", data)})
        assert response.status_code == 200
        assert response.json()["grids"][0]["matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        assert response.json()["inspection_image"].startswith("data:image/png;base64,")
        assert client.get("/api/jobs", headers=HEADERS).json()["jobs"] == []


def test_opt_in_passes_matrices_and_original_image_to_provider(tmp_path):
    class CaptureProvider:
        async def analyze(self, image, text, mode, prompt, operation, question_type):
            assert image
            assert "本地像素读格的候选数据" in text
            assert '"matrix": [[1, 0], [0, 1]]' in text
            return {"question": "fixture", "answer": "fixture", "steps": [], "cautions": []}

    with TestClient(create_app(Settings(), TOKEN, CaptureProvider(), tmp_path)) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"token": TOKEN})
            socket.receive_json()
            response = client.post(
                "/api/screenshots",
                headers=HEADERS,
                files={"image": ("grid.png", picture([[[1, 0], [0, 1]]]))},
                data={"grid_reading": "true", "mode": "visual"},
            )
            assert response.status_code == 202
            while True:
                event = socket.receive_json()
                if event["type"] == "job" and event["job"]["status"] in {"done", "error"}:
                    job = event["job"]
                    break
            assert job["status"] == "done"
            assert job["grid_report"]["grids"][0]["matrix"] == [[1, 0], [0, 1]]
            assert (
                client.get(f"/api/jobs/{job['id']}/grid-image", headers=HEADERS).status_code == 200
            )
            assert client.get(f"/api/jobs/{job['id']}/grid-image").status_code == 401
