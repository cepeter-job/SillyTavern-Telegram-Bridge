"""Static call-site guard complements runtime omission of third-party details."""

from pathlib import Path

from tools.audit_logging_privacy import inventory, inventory_file


def test_inventory_identifies_fstrings_joined_strings_aliases_and_variable_messages(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text('''import logging as log
from logging import warning as warn
logger = log.getLogger(__name__)
log.info("safe %s", user_text)
log.error(f"unsafe {user_text}")
warn("unsafe " + user_text)
logger.debug(user_text)
log.getLogger(user_text)
''')
    report = inventory(tmp_path)
    assert report["files"] == 1
    assert [row["line"] for row in report["unsafe"]] == [5, 6, 7, 8]
    assert len(report["calls"]) == 6


def test_nonlogging_candidates_remain_visible_without_claiming_they_are_loggers(tmp_path):
    source = tmp_path / "maths.py"
    source.write_text("import math\nvalue = math.log(x)\n")
    rows = inventory_file(source, tmp_path)
    assert len(rows) == 1
    assert rows[0]["classification"] == "nonlogging_candidate"
    assert rows[0]["unsafe"] is False


def test_arbitrarily_named_logger_instances_and_aliases_are_resolved(tmp_path):
    source = tmp_path / "aliases.py"
    source.write_text("import logging as lg\nsink = lg.getLogger('st')\ncopy = sink\ncopy.error(user_text)\n")
    rows = inventory(tmp_path)["unsafe"]
    assert len(rows) == 1
    assert rows[0]["owner"] == "copy"
    assert rows[0]["line"] == 4


def test_complete_production_logging_inventory_has_no_unsafe_message_expressions():
    root = Path(__file__).resolve().parents[1] / "bridge"
    report = inventory(root)
    assert report["files"] > 0
    assert report["calls"], "The production audit must actually enumerate logging calls"
    assert report["unsafe"] == [], report["unsafe"]
