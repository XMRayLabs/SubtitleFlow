import unittest
from subtitleflow.srt import Cue
from subtitleflow.merge import merge, MergeOptions

class DurationTests(unittest.TestCase):
    def test_distant_punctuation_cannot_extend_group(self):
        cues=[Cue(i+1,i*1000,(i+1)*1000,"fragment" if i<199 else "end.") for i in range(200)]
        for boundaries in (None,{199}):
            result=merge(cues,MergeOptions(),boundaries)
            self.assertTrue(all(c.end-c.start<=13000 for c in result))
            self.assertEqual(sum(c.text.count("fragment") for c in result),199)
            self.assertEqual(result[-1].end,200000)

    def test_original_long_cue_kept_separate(self):
        result=merge([Cue(1,0,20000,"long"),Cue(2,20000,21000,"end.")],MergeOptions())
        self.assertEqual([(c.start,c.end) for c in result],[(0,20000),(20000,21000)])
