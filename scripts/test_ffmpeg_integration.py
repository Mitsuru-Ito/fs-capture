"""Required CI runner: missing tools or skipped selected tests is a failure."""
from pathlib import Path
import shutil
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / 'tests')]
if not all(shutil.which(name) for name in ('ffmpeg', 'ffprobe')):
    raise SystemExit('Required FFmpeg/ffprobe unavailable')
names = [
    'test_pipeline.PipelineTests.test_real_ffmpeg_dual_track_extraction',
    'test_diagnostics.DecodeIntegrationTests.test_actual_two_track_first_frame_decode',
    'test_collection.CollectionTests',
    'test_derive.DeriveCollectionTests',
    'test_field_media.FieldMediaTests',
    'test_field_evidence.EvidenceCliTests',
]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
expected = suite.countTestCases()
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun == expected and expected >= 10 and not result.skipped else 1)
