import unittest

from transcriber.segments import Cue, Segment, finalize_cues, parse_output, plan_segments


class PlanSegmentsTests(unittest.TestCase):
    def test_cuts_at_silence_nearest_to_each_target(self):
        silences = [(120.0, 121.0), (599.0, 600.4), (1201.0, 1201.2)]
        self.assertEqual(plan_segments(1864.704, silences, 600), [
            Segment(0.0, 599.7, False),
            Segment(599.7, 1201.1, False),
            Segment(1201.1, 1864.704, False),
        ])

    def test_prefers_closer_silence_within_tolerance(self):
        # Midpoints 500 and 650: 650 is 50s from the 600s target, 500 is 100s away.
        segments = plan_segments(1000, [(499, 501), (649, 651)], 600)
        self.assertEqual(segments[0], Segment(0.0, 650.0, False))

    def test_forces_cut_when_no_silence_in_window(self):
        self.assertEqual(plan_segments(1864.704, [(100, 101)], 600), [
            Segment(0.0, 600.0, True),
            Segment(600.0, 1200.0, True),
            Segment(1200.0, 1864.704, False),
        ])

    def test_short_audio_is_one_segment(self):
        self.assertEqual(plan_segments(100, [(10, 12)], 300), [Segment(0.0, 100, False)])


class ParseOutputTests(unittest.TestCase):
    def test_shifts_timestamps_to_whole_file_time(self):
        raw = "[0.00][S01] Szia, én Zsuzsa vagyok.[1.92][1.92][S02] Második  mondat.[5.82]"
        self.assertEqual(parse_output(raw, 600), [
            Cue(600000, 601920, "Szia, én Zsuzsa vagyok."),
            Cue(601920, 605820, "Második mondat."),
        ])

    def test_ignores_malformed_and_empty_entries(self):
        raw = "noise [1.00][S01]   [2.00][3.00][S01] ok[4.00][5.00][S01] truncated"
        self.assertEqual(parse_output(raw, 0), [Cue(3000, 4000, "ok")])


class FinalizeCuesTests(unittest.TestCase):
    def test_sorts_and_clips_overlap_to_next_start(self):
        cues = [Cue(900, 2000, "b"), Cue(0, 1000, "a")]
        self.assertEqual(finalize_cues(cues), [Cue(0, 900, "a"), Cue(900, 2000, "b")])

    def test_zero_length_cue_gets_minimum_duration(self):
        self.assertEqual(finalize_cues([Cue(3000, 3000, "x")]), [Cue(3000, 3300, "x")])


if __name__ == "__main__":
    unittest.main()
