"""Tests for _parse_ctf_ids — CTF token ID validation."""

import json

from core.positions.service import PositionService


class TestParseCTFIds:
    """Tests for PositionService._parse_ctf_ids static method."""

    def test_empty_string(self):
        assert PositionService._parse_ctf_ids("") == []

    def test_none_input(self):
        assert PositionService._parse_ctf_ids(None) == []

    def test_empty_json_array(self):
        """The '[]' bug — must not treat as valid."""
        assert PositionService._parse_ctf_ids("[]") == []

    def test_single_id_rejected(self):
        """Partition [1,2] always produces 2 — reject 1."""
        assert PositionService._parse_ctf_ids(json.dumps(["id1"])) == []

    def test_exactly_two_ids_accepted(self):
        """Normal case — [YES_id, NO_id]."""
        ids = ["111", "222"]
        assert PositionService._parse_ctf_ids(json.dumps(ids)) == ids

    def test_three_ids_rejected(self):
        """More than 2 IDs is unexpected for our partition — reject."""
        ids = ["111", "222", "333"]
        assert PositionService._parse_ctf_ids(json.dumps(ids)) == []

    def test_non_list_rejected(self):
        assert PositionService._parse_ctf_ids(json.dumps({"a": "b"})) == []
        assert PositionService._parse_ctf_ids(json.dumps("not-a-list")) == []

    def test_malformed_json(self):
        assert PositionService._parse_ctf_ids("{broken") == []

    def test_real_token_ids(self):
        """Real-world CTF token IDs from Polymarket split."""
        ids = [
            "50595074383087715828954192200809402865931709644287359962942231873226051716104",
            "114295305505917331875803008704363538044645291752480399108380287982458621083129",
        ]
        result = PositionService._parse_ctf_ids(json.dumps(ids))
        assert result == ids
        assert len(result) == 2
