"""Tests for cloudfit.disk — dynamic disk sizing formula."""

import pytest
from cloudfit.disk import (
    compute_disk_tb,
    compute_disk_breakdown,
    list_sequencers,
    _COMPRESSION_FACTOR,
)


class TestComputeDiskTb:
    def test_novaseq_s4_four_lanes_returns_expected_range(self):
        # 4-lane high-output run — real-world expectation: ~16–20 TB
        tb = compute_disk_tb("novaseq_6000", "s4", lanes=4)
        assert 14 <= tb <= 22, f"Expected 14–22 TB for S4/4L, got {tb}"

    def test_miseq_nano_is_small(self):
        # Nano is a tiny run — should be well under 5 GB
        tb = compute_disk_tb("miseq", "nano", lanes=1)
        assert tb < 0.01, f"MiSeq Nano should be < 0.01 TB, got {tb}"

    def test_compressed_input_smaller_than_uncompressed_equivalent(self):
        # Compressed input format is 4x smaller — input_gb should be lower
        compressed_bd   = compute_disk_breakdown("novaseq_x", "25b", lanes=4)
        uncompressed_bd = compute_disk_breakdown("novaseq_6000", "s4", lanes=4)
        assert compressed_bd.input_gb < uncompressed_bd.input_gb * 0.5

    def test_retain_input_increases_disk(self):
        base     = compute_disk_tb("novaseq_6000", "s4", lanes=4, retain_input=False)
        retained = compute_disk_tb("novaseq_6000", "s4", lanes=4, retain_input=True)
        assert retained > base

    def test_keep_undetermined_increases_disk(self):
        base  = compute_disk_tb("novaseq_6000", "s4", lanes=4, keep_undetermined=False)
        with_ = compute_disk_tb("novaseq_6000", "s4", lanes=4, keep_undetermined=True)
        assert with_ > base

    def test_safety_margin_applied(self):
        no_margin   = compute_disk_breakdown("novaseq_6000", "s1", lanes=2, safety_margin=0.0)
        with_margin = compute_disk_breakdown("novaseq_6000", "s1", lanes=2, safety_margin=0.20)
        assert abs(with_margin.total_gb - no_margin.subtotal_gb * 1.20) < 1

    def test_lanes_capped_at_max(self):
        # MiSeq has max 1 lane — passing lanes=4 should equal lanes=1
        one  = compute_disk_tb("miseq", "standard", lanes=1)
        four = compute_disk_tb("miseq", "standard", lanes=4)
        assert one == four

    def test_unknown_sequencer_raises(self):
        with pytest.raises(ValueError, match="Unknown sequencer"):
            compute_disk_tb("unknown_seq", "s4", lanes=1)

    def test_unknown_flowcell_raises(self):
        with pytest.raises(ValueError, match="Unknown flowcell"):
            compute_disk_tb("novaseq_6000", "x99", lanes=1)


class TestDiskBreakdown:
    def test_breakdown_components_sum_to_subtotal(self):
        bd = compute_disk_breakdown("novaseq_6000", "s4", lanes=4)
        expected = (
            bd.input_gb + bd.output_gb + bd.tmp_gb + bd.retained_input_gb
        )
        assert abs(bd.subtotal_gb - expected) < 0.5

    def test_total_tb_property(self):
        bd = compute_disk_breakdown("novaseq_6000", "s4", lanes=4)
        assert abs(bd.total_tb - bd.total_gb / 1000) < 0.001

    def test_no_retained_input_by_default(self):
        bd = compute_disk_breakdown("novaseq_6000", "s4", lanes=4)
        assert bd.retained_input_gb == 0.0

    def test_compression_factor_applied_to_input_only(self):
        bd = compute_disk_breakdown("novaseq_x", "10b", lanes=4)
        # input_gb should be reduced by compression factor vs raw gb_per_lane
        raw_gb_per_lane = 600.0  # from profile
        expected_input = raw_gb_per_lane * 4 * _COMPRESSION_FACTOR
        assert abs(bd.input_gb - expected_input) < 1


class TestListSequencers:
    def test_returns_known_sequencers(self):
        known = list_sequencers()
        assert "miseq" in known
        assert "novaseq_6000" in known
        assert "novaseq_x" in known

    def test_each_sequencer_has_flowcells(self):
        for seq, flowcells in list_sequencers().items():
            assert len(flowcells) > 0, f"{seq} has no flowcells"
