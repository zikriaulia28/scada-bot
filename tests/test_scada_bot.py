import unittest, time
import sys, os
# ensure parent directory on path
sys.path.append(os.path.abspath('..'))

from scada_bot.session import (
    create_session,
    get_session,
    delete_session,
    _sessions,
    cleanup_timeout_sessions,
    calculate_progress,
    build_status_text,
    build_summary_text,
)
from scada_bot.config import SESSION_TIMEOUT_MINUTES, FIELD_MAP
from scada_bot.sheets import merge_ocr

class TestScadaBot(unittest.TestCase):
    def setUp(self):
        _sessions.clear()

    def test_create_and_delete_session(self):
        chat_id = 'user1'
        sess = create_session(chat_id, 3)
        self.assertEqual(sess['time'], 3)
        self.assertIn(chat_id, _sessions)
        self.assertTrue(delete_session(chat_id))
        self.assertNotIn(chat_id, _sessions)

    def test_merge_ocr_and_progress(self):
        chat_id = 'user2'
        sess = create_session(chat_id, 5)
        # first OCR data — pakai field names baru
        data1 = {'pit_100': 10, 'pit_101': None, 'gc_a_actual_btu': 100}
        changes1 = merge_ocr(sess, data1)
        self.assertEqual(sess['ocr']['pit_100'], 10)
        self.assertEqual(sess['ocr']['gc_a_actual_btu'], 100)
        self.assertEqual(changes1, [])  # no previous value
        # second OCR data with overlapping key
        data2 = {'pit_100': 12, 'pit_101': 20, 'gc_a_actual_btu': None}
        changes2 = merge_ocr(sess, data2)
        self.assertEqual(sess['ocr']['pit_100'], 12)  # overwritten
        self.assertEqual(sess['ocr']['pit_101'], 20)
        self.assertEqual(sess['ocr']['gc_a_actual_btu'], 100)  # unchanged (None)
        self.assertIn('pit_100: 10 → 12', changes2)
        # check progress
        filled, total, missing = calculate_progress(sess)
        self.assertEqual(filled, 3)  # pit_100, pit_101, gc_a_actual_btu
        self.assertEqual(total, len(FIELD_MAP))
        self.assertIn('Loop A %', missing)

    def test_status_and_summary_text(self):
        chat_id = 'user3'
        sess = create_session(chat_id, 7)
        sess['ocr'] = {'pit_100': 5, 'gc_a_actual_btu': 200}
        sess['photo_count'] = 2
        sess['changes_log'] = ['pit_100: 4 → 5']
        status = build_status_text(sess)
        self.assertIn('Time  : `7`', status)
        self.assertIn('Foto  : 2 diterima', status)
        self.assertIn('PIT 100: 5 ✔', status)
        self.assertIn('GC A: 200 ✔', status)
        summary = build_summary_text(sess, sess['changes_log'])
        self.assertIn('Data Time 7 disimpan', summary)
        self.assertIn('Baris: `11`', summary)
        self.assertIn('Foto diproses: `2`', summary)
        self.assertIn('pit_100: 4 → 5', summary)

    def test_session_timeout(self):
        chat_id = 'user4'
        sess = create_session(chat_id, 1)
        sess['created_at'] = time.time() - (SESSION_TIMEOUT_MINUTES + 1) * 60
        cleanup_timeout_sessions()
        self.assertNotIn(chat_id, _sessions)

    def test_field_map_count(self):
        """Pastikan FIELD_MAP sesuai jumlah kolom sheet (21)."""
        self.assertEqual(len(FIELD_MAP), 21)

if __name__ == '__main__':
    unittest.main()
