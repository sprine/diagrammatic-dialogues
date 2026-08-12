from src.asciigrid import audit, parse, repair


def labels(d):
    return sorted(n.label for n in d.nodes)


def links(d):
    by_id = {n.id: n.label for n in d.nodes}
    return sorted((by_id[e.source], by_id[e.target]) for e in d.edges)


def test_two_boxes_one_arrow():
    d = parse(
        """
+--------+     +--------+
| Client |---->| Server |
+--------+     +--------+
"""
    )
    assert labels(d) == ["Client", "Server"]
    assert links(d) == [("Client", "Server")]
    assert d.edges[0].points[0][0] == 9  # leaves the Client border
    assert d.edges[0].points[-1][0] == 15  # lands on the Server border


def test_fanout_shares_one_trunk():
    d = parse(
        """
+----------+     +---------------+     +-------------+
| Client   |---->| Load Balancer |---->| App 1       |
+----------+     +---------------+     +-------------+
                         |
                         |             +-------------+
                         +------------>| App 2       |
                                       +-------------+
"""
    )
    assert links(d) == [
        ("Client", "Load Balancer"),
        ("Load Balancer", "App 1"),
        ("Load Balancer", "App 2"),
    ]


def test_elbow_route_follows_the_drawing():
    d = parse(
        """
+-----+
| A   |
+-----+
   |
   |   +-----+
   +-->| B   |
       +-----+
"""
    )
    assert links(d) == [("A", "B")]
    pts = d.edges[0].points
    assert pts == [(3, 2), (3, 5), (7, 5)]  # down the trunk, elbow, into B


def test_nested_group_is_a_parent():
    d = parse(
        """
+---------------------+
| api                 |
|   +-------------+   |
|   | handler     |   |
|   +-------------+   |
+---------------------+
"""
    )
    outer = next(n for n in d.nodes if n.label.startswith("api"))
    inner = next(n for n in d.nodes if n.label == "handler")
    assert inner.parent == outer.id
    assert "handler" not in outer.label


def test_inline_edge_label_keeps_one_connector():
    d = parse(
        """
+-----+                +-------+
| web |---- token ---->| authz |
+-----+                +-------+
"""
    )
    assert links(d) == [("web", "authz")]
    assert d.edges[0].label == "token"


def test_bidirectional():
    d = parse(
        """
+-----+          +-----+
|  a  |<-------->|  b  |
+-----+          +-----+
"""
    )
    assert len(d.edges) == 1
    assert d.edges[0].bidirectional


def test_free_text_becomes_a_note():
    d = parse(
        """
this is a caption that stands on its own

+-----+
|  a  |
+-----+
"""
    )
    assert [n.text for n in d.notes] == ["this is a caption that stands on its own"]


def test_neighbours_reads_out_context():
    d = parse(
        """
+-----+     +-----+     +-----+
|  a  |---->|  b  |---->|  c  |
+-----+     +-----+     +-----+
"""
    )
    b = next(n for n in d.nodes if n.label == "b")
    assert sorted(d.neighbours(b.id)) == ["a", "c"]


def test_empty_input_is_not_a_crash():
    d = parse("")
    assert d.nodes == [] and d.cols == 0


def test_prose_is_not_mistaken_for_wiring():
    # the `v` in "views" is not an arrowhead; the `-` in "read-only" is not a wire
    d = parse(
        """
all views are read-only

+-----+
|  a  |
+-----+
"""
    )
    assert d.edges == []
    assert [n.text for n in d.notes] == ["all views are read-only"]


def test_audit_is_quiet_on_a_clean_drawing():
    assert audit("+-----+\n|  a  |\n+-----+\n") == []


def test_repair_widens_a_box_its_label_overran():
    broken = "+-----------+\n|PuzzleEngine|\n+-----------+\n"
    assert parse(broken).nodes == []
    fixed = parse(repair(broken))
    assert [n.label for n in fixed.nodes] == ["PuzzleEngine"]


def test_repair_straightens_a_box_that_drifted_a_column():
    # bottom border and last rows shifted right by one, junction in the border
    broken = (
        "+----------------+\n"
        "|    AppModel    |\n"
        " |  Screen enum  |\n"
        " +-------+-------+\n"
    )
    assert parse(broken).nodes == []
    fixed = parse(repair(broken))
    assert len(fixed.nodes) == 1
    assert fixed.nodes[0].label == "AppModel\nScreen enum"


def test_repair_leaves_a_good_drawing_alone():
    good = "+--------+     +--------+\n| Client |---->| Server |\n+--------+     +--------+"
    assert repair(good) == good


def test_repair_keeps_the_connectors_attached():
    broken = (
        "+--------+     +----------+\n"
        "| Client |---->| Server   |\n"
        "+--------+      | extra   |\n"
        "                +---------+\n"
    )
    fixed = parse(repair(broken))
    assert len(fixed.nodes) == 2
    assert len(fixed.edges) == 1


def test_a_caption_across_a_vertical_wire_is_its_label():
    d = parse(
        """
+-------+
|   a   |
+---+---+
    |
 writes
    v
+-------+
|   b   |
+-------+
"""
    )
    assert len(d.edges) == 1
    assert d.edges[0].label == "writes"
    assert d.notes == []


def test_audit_catches_an_oversized_drawing():
    assert any("rows tall" in p for p in audit("\n".join(["x"] * 40)))


def test_garbage_input_degrades_to_notes():
    d = parse("I could not draw this, sorry.")
    assert d.nodes == []
    assert d.notes and "could not draw" in d.notes[0].text


def test_underscore_bus_fans_in():
    d = parse(
        """
 +-----+  +-----+  +-----+
 |  a  |  |  b  |  |  c  |
 +--+--+  +--+--+  +--+--+
    \\_______|________/
            |
            v
      +------------+
      |   store    |
      +------------+
"""
    )
    assert {t for _, t in links(d)} == {"store"}
    assert {s for s, _ in links(d)} == {"a", "b", "c"}
    assert d.notes == []


def test_fatal_only_ignores_a_merely_oversized_drawing():
    tall = "\n".join(["+-----+", "|  a  |", "+-----+"] + ["x"] * 40)
    assert any("rows tall" in p for p in audit(tall))
    assert audit(tall, fatal_only=True) == []


def test_fatal_only_still_catches_a_missing_box():
    broken = "+-----------+\n|PuzzleEngine|\n+-----------+\n"
    assert audit(broken, fatal_only=True)


def test_fatal_catches_a_box_whose_stray_pipes_read_as_wiring():
    # the leftover `|` get absorbed as connectors, so only the box finder notices
    broken = (
        "+--------------+   +----------------+\n"
        "|claude_cli.py |-->|   web.py       |\n"
        "| subprocess   |   |  _drive()      |\n"
        "+--------------+   |  async for     |\n"
        "                    +----------------+\n"
    )
    assert len(parse(broken).nodes) < 2
    assert audit(broken, fatal_only=True)
    assert len(parse(repair(broken)).nodes) == 2


def test_a_caption_wrapped_over_two_lines_keeps_the_connector():
    d = parse(
        """
+-------+
|   a   |
+---+---+
    |
 q.put_nowait(event) per
 subscriber queue
    v
+-------+
|   b   |
+-------+
"""
    )
    assert len(d.edges) == 1
    assert d.edges[0].label == "q.put_nowait(event) per subscriber queue"
    assert d.notes == []


def test_a_caption_between_the_arrow_and_its_box_still_lands():
    d = parse(
        """
+-------+
|   a   |
+---+---+
    v
 GET /things/{id}
+---------+
|    b    |
+---------+
"""
    )
    assert len(d.edges) == 1
    assert "GET /things" in d.edges[0].label


def test_text_under_a_horizontal_arrow_is_not_a_vertical_caption():
    # the label belongs to the arrow above it; nothing should be joined downward
    d = parse(
        """
+-------+           +-------+
|   a   |<----------|   b   |
|       |    SSE    |       |
+-------+   events  +-------+
"""
    )
    assert [(e.source, e.target) for e in d.edges] == [(d.nodes[1].id, d.nodes[0].id)]


def test_a_caption_on_a_connector_that_reaches_nothing_stays_text():
    # bridging masks the words as wire so they do not split the run; this run
    # reaches only one box, so it becomes no edge and the words go with it
    d = parse(
        """
+-------+
|   a   |
+---+---+
    |
 job.finished = True
    v
"""
    )
    assert d.edges == []
    assert [n.text for n in d.notes] == ["job.finished = True"]

    # the counter-case: give the run a second box and the caption is its label
    d = parse(
        """
+-------+
|   a   |
+---+---+
    |
 job.finished = True
    v
+-------+
|   b   |
+-------+
"""
    )
    assert d.edges[0].label == "job.finished = True"
    assert d.notes == []


def test_a_second_caption_on_one_connector_stays_text():
    # one connector carries one label; whatever it does not take is still text
    d = parse(
        """
+-------+
|   a   |
+---+---+
    |
 writes
    v
 then reads
+---------+
|    b    |
+---------+
"""
    )
    assert len(d.edges) == 1
    assert d.edges[0].label == "writes"
    assert [n.text for n in d.notes] == ["then reads"]


def test_repair_closes_a_box_taller_than_a_few_rows():
    # a component box with many lines of detail; the right border is ragged
    body = "\n".join(f"|  line {i} of detail{' ' * (i % 3)}|" for i in range(9))
    broken = "+" + "-" * 22 + "+\n" + body + "\n+" + "-" * 22 + "+\n"
    assert parse(broken).nodes == []
    assert audit(broken, fatal_only=True)
    assert len(parse(repair(broken)).nodes) == 1
