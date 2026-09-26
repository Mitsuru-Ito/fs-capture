"""Import transactions: synthetic PNGs, no visual-quality claim."""
from pathlib import Path
from unittest.mock import patch
import unittest
import test_preview as fixtures
from fs_capture import core as c, preview as p

class ImportIdentityTests(unittest.TestCase):
    setUp=fixtures.PreviewTests.setUp
    tearDown=fixtures.PreviewTests.tearDown
    prepare=fixtures.PreviewTests.prepare
    record=fixtures.PreviewTests.record

    def test_legacy_retry_and_true_reobservation(self):
        folder=self.prepare(); source=self.root/'review.json'; record=self.record(folder)
        c.write(source,record); first=p.import_review(folder,source)
        self.assertEqual(first['review'],p.import_review(folder,source)['review'])
        self.assertEqual(len(list((folder/'reviews').glob('*.json'))),1)
        record['at']='2026-09-26T00:00:00Z'; c.write(source,record)
        self.assertNotEqual(first['review'],p.import_review(folder,source)['review'])

    def test_explicit_identity_conflicts_and_new_observation(self):
        folder=self.prepare(); source=self.root/'review.json'; record=self.record(folder)
        record.update(observationId='observation-1',exportId='export-1')
        c.write(source,record); first=p.import_review(folder,source)
        record['exportId']='export-retry';c.write(source,record)
        self.assertEqual(first['review'],p.import_review(folder,source)['review'])
        record['reason']='different';c.write(source,record)
        with self.assertRaisesRegex(c.CaptureError,'conflict'):p.import_review(folder,source)
        record.update(observationId='observation-2',exportId='export-2');c.write(source,record)
        self.assertNotEqual(first['review'],p.import_review(folder,source)['review'])

    def test_interrupted_metadata_is_not_committed(self):
        folder=self.prepare();source=self.root/'review.json';c.write(source,self.record(folder))
        with patch.object(c,'write',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):p.import_review(folder,source)
        self.assertEqual(list((folder/'reviews').glob('*.json')),[])
        first=p.import_review(folder,source)
        self.assertEqual(first['review'],p.import_review(folder,source)['review'])

class ComparisonImportIdentityTests(unittest.TestCase):
    setUp=fixtures.PreviewTests.setUp
    tearDown=fixtures.PreviewTests.tearDown
    prepare=fixtures.PreviewTests.prepare
    record=fixtures.PreviewTests.record
    pair=fixtures.ComparisonTests.pair
    def test_schema2_retry_and_wrong_candidate(self):
        a,b=self.pair();folder=p.compare(a,b,self.root/'compare')
        record=self.record(folder);candidate=c.read(folder/'scene.json')['comparison']['candidates'][1]
        record.update(schemaVersion=2,candidateId='B',modelSha256=candidate['modelSha256'],sourceBundleId=candidate['sourceBundleId'])
        source=self.root/'review.json';c.write(source,record);first=p.import_review(folder,source)
        self.assertEqual(first['review'],p.import_review(folder,source)['review'])
        record['candidateId']='A';c.write(source,record)
        with self.assertRaises(c.CaptureError):p.import_review(folder,source)

class ExportAliasTests(unittest.TestCase):
    setUp=fixtures.PreviewTests.setUp
    tearDown=fixtures.PreviewTests.tearDown
    prepare=fixtures.PreviewTests.prepare
    record=fixtures.PreviewTests.record
    def test_reused_export_alias_conflicts(self):
        folder=self.prepare();source=self.root/'review.json';record=self.record(folder)
        record.update(observationId='obs1',exportId='first');c.write(source,record);p.import_review(folder,source)
        record['exportId']='retry';c.write(source,record);p.import_review(folder,source)
        record['observationId']='obs2';c.write(source,record)
        with self.assertRaisesRegex(c.CaptureError,'conflict'):p.import_review(folder,source)
