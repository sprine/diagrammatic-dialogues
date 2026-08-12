"""The inbox files a bad render; it never touches the renderer."""

import json

from src import reports

CARD = {
    "id": "abc-123",
    "title": "Preload Bridge Detail",
    "model": "sonnet",
    "effort": "medium",
    "remark": "what happens here?",
    # right border ragged: repair straightens it, so both forms are worth keeping
    "ascii": "+--------+\n| alpha   |\n+--------+\n     |\n     v\n+--------+\n| beta   |\n+--------+\n",
}


def file_one(tmp_path, monkeypatch, description="the arrow points the wrong way"):
    monkeypatch.setattr(reports, "INBOX", tmp_path)
    path = reports.write(CARD, description)
    return path, json.loads(path.read_text())


def test_a_report_is_a_record_beside_the_picture(tmp_path, monkeypatch):
    path, record = file_one(tmp_path, monkeypatch)
    assert path.parent == tmp_path
    assert record["description"] == "the arrow points the wrong way"
    assert record["card"]["title"] == "Preload Bridge Detail"
    svg = tmp_path / record["svg"]
    assert svg.exists() and svg.read_text().startswith("<svg")


def test_it_keeps_what_the_model_drew_as_well_as_what_was_rendered(tmp_path, monkeypatch):
    _, record = file_one(tmp_path, monkeypatch)
    assert record["ascii_as_drawn"] == CARD["ascii"]      # ragged, as it arrived
    assert record["ascii"] != record["ascii_as_drawn"]    # straightened, as drawn


def test_it_records_what_the_pipeline_made_of_the_drawing(tmp_path, monkeypatch):
    _, record = file_one(tmp_path, monkeypatch)
    produced = record["produced"]
    assert produced["boxes"] == ["alpha", "beta"]
    assert produced["edges"] == [{"from": "alpha", "to": "beta", "label": ""}]
    assert produced["unconnected_boxes"] == []


def test_the_filename_says_what_it_is(tmp_path, monkeypatch):
    path, _ = file_one(tmp_path, monkeypatch)
    assert path.name.endswith("-preload-bridge-detail.json")
    assert path.name[:4].isdigit()  # sorts by when it was filed
