from turku3d.observe import transport


def test_content_puts_each_file_after_its_name_and_the_task_last(tmp_path):
    (tmp_path / "FORMAT.md").write_text("the format")
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff")
    blocks = transport.content([tmp_path / "FORMAT.md", tmp_path / "a.jpg"], "the task")
    assert [b["type"] for b in blocks] == ["text", "text", "text", "image", "text"]
    assert blocks[1]["text"].endswith("FORMAT.md ===\nthe format")
    assert blocks[2]["text"].endswith("a.jpg ===")
    assert blocks[3]["source"]["media_type"] == "image/jpeg"
    assert blocks[-1]["text"] == "the task"


def test_cost_counts_cache_writes_and_reads_at_their_rates():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 1_000_000}
    assert transport.cost("claude-fable-5-2", usage) == 8.0 + 4.0 + 0.8


def test_problems_name_the_place_of_each_schema_error():
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}, "required": ["n"]}
    assert transport.problems(schema, {"n": 2}) == []
    assert transport.problems(schema, {"n": 0})[0].startswith("n: 0 is less than the minimum")
    assert transport.problems(schema, {})[0].startswith("(root): 'n' is a required property")
