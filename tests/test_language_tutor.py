import unittest
from unittest.mock import MagicMock
from xiaozhi.services.language_tutor_service import (
    save_vocabulary_flashcard,
    get_stored_vocabulary_list,
    quiz_language_vocabulary,
    start_conversation_roleplay,
    explain_grammar_and_nuance,
    format_vocabulary_flashcard_markdown,
)
from xiaozhi.mcp.context import mcp_active_owner_ctx


class TestLanguageTutorService(unittest.TestCase):
    def setUp(self):
        self.mock_store = MagicMock()
        self.owner_id = 99

    def test_format_vocabulary_flashcard_markdown(self):
        md = format_vocabulary_flashcard_markdown(
            word="Accomplish",
            language="Inggris",
            translation="Menyelesaikan / Berhasil mencapai",
            pronunciation="uh-KAHM-plish",
            part_of_speech="Verb",
            level="B1",
            example="We accomplished the mission.",
            example_translation="Kita telah menyelesaikan misi tersebut.",
            notes="Gunakan saat mencapai target penting.",
        )
        self.assertIn("# 📇 Flashcard Kosakata: Accomplish", md)
        self.assertIn("> 🗣️ **Pelafalan / Fonetik**: `uh-KAHM-plish`", md)
        self.assertIn("> 📚 **Tingkat Kemahiran**: B1", md)
        self.assertIn("We accomplished the mission.", md)
        self.assertIn("Gunakan saat mencapai target penting.", md)
        self.assertIn("Latihan Cepat XiaoZhi", md)

    def test_save_vocabulary_flashcard(self):
        self.mock_store.search_materials.return_value = []
        self.mock_store.add_material.return_value = 101

        res = save_vocabulary_flashcard(
            store=self.mock_store,
            owner_id=self.owner_id,
            word="Convenient",
            language="Inggris",
            translation="Praktis / Mudah",
            pronunciation="kuhn-VEE-nyuhnt",
            level="A2",
            example="Living here is convenient.",
            example_translation="Tinggal di sini praktis.",
        )
        self.assertTrue(res["success"])
        self.assertFalse(res["already_existed"])
        self.assertEqual(res["material_id"], 101)
        self.mock_store.add_material.assert_called_once()
        args, kwargs = self.mock_store.add_material.call_args
        self.assertEqual(kwargs.get("category") or args[2], "Buku Kosakata - Inggris")
        self.assertEqual(kwargs.get("source_type") or args[5], "language_flashcard")

    def test_save_vocabulary_flashcard_duplicate_prevention(self):
        self.mock_store.search_materials.return_value = [{
            "id": 55,
            "title": "Convenient (Praktis)",
            "category": "Buku Kosakata - Inggris",
        }]

        res = save_vocabulary_flashcard(
            store=self.mock_store,
            owner_id=self.owner_id,
            word="Convenient",
            language="Inggris",
            translation="Praktis",
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["already_existed"])
        self.assertEqual(res["material_id"], 55)
        self.assertIn("sudah tersimpan", res["message"])
        self.mock_store.add_material.assert_not_called()

    def test_quiz_language_vocabulary_ask_and_eval(self):
        self.mock_store.list_materials.return_value = []

        # 1. Ask Question 1 (from starter bank)
        q1 = quiz_language_vocabulary(
            store=self.mock_store,
            owner_id=self.owner_id,
            language="Inggris",
            question_number=1,
            user_answer="",
        )
        self.assertTrue(q1["success"])
        self.assertEqual(q1["mode"], "tanya_soal")
        self.assertEqual(q1["soal_ke"], 1)
        self.assertTrue(len(q1["kata_target"]) > 0)

        # 2. Evaluate User Answer
        eval1 = quiz_language_vocabulary(
            store=self.mock_store,
            owner_id=self.owner_id,
            language="Inggris",
            question_number=1,
            user_answer="Artinya menyelesaikan target pekerjaan",
        )
        self.assertTrue(eval1["success"])
        self.assertEqual(eval1["mode"], "evaluasi_jawaban")
        self.assertEqual(eval1["soal_ke"], 1)
        self.assertTrue(eval1["ada_soal_berikutnya"])
        self.assertEqual(eval1["nomor_soal_berikutnya"], 2)
        self.assertIn("PANDUAN MENILAI JAWABAN", eval1["instruksi_ai"])

    def test_start_conversation_roleplay(self):
        # 1. Start Cafe Roleplay
        init_rp = start_conversation_roleplay(
            scenario="kafe",
            target_language="Inggris",
            level="Pemula",
            turn_number=1,
            user_speech="",
        )
        self.assertTrue(init_rp["success"])
        self.assertEqual(init_rp["mode"], "mulai_roleplay")
        self.assertIn("Brew & Co", init_rp["kalimat_pembuka_target"])
        self.assertIn("iced latte", init_rp["pilihan_jawaban_pengguna"][0].lower())

        # 2. Continue Turn with User Speech (Feedback-first)
        next_rp = start_conversation_roleplay(
            scenario="kafe",
            target_language="Inggris",
            level="Pemula",
            turn_number=2,
            user_speech="I want one iced latte with oat milk please",
        )
        self.assertTrue(next_rp["success"])
        self.assertEqual(next_rp["mode"], "lanjutkan_roleplay")
        self.assertEqual(next_rp["giliran_ke"], 2)
        self.assertFalse(next_rp["apakah_sesi_terakhir"])
        self.assertIn("FEEDBACK-FIRST PEDAGOGY", next_rp["instruksi_ai"])

        # 3. Final Turn
        final_rp = start_conversation_roleplay(
            scenario="kafe",
            target_language="Inggris",
            level="Pemula",
            turn_number=4,
            user_speech="Thank you so much! Here is the cash.",
        )
        self.assertTrue(final_rp["apakah_sesi_terakhir"])
        self.assertIn("selesai dengan sukses", final_rp["instruksi_ai"])

    def test_explain_grammar_and_nuance(self):
        res = explain_grammar_and_nuance("present perfect vs past simple", target_language="Inggris")
        self.assertTrue(res["success"])
        self.assertIn("Present Perfect", res["topik"])
        self.assertIn("Past Simple", res["topik"])
        self.assertIn("yesterday", res["kesalahan_umum"].lower())

        # Custom topic
        res_custom = explain_grammar_and_nuance("conditional sentence type 3", target_language="Inggris")
        self.assertTrue(res_custom["success"])
        self.assertEqual(res_custom["topik"], "conditional sentence type 3")

    def test_mcp_language_tools_registration(self):
        from xiaozhi.mcp.tools import register_tools

        registered = {}
        mock_mcp = MagicMock()
        def mock_tool():
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator
        mock_mcp.tool = mock_tool

        mock_store = MagicMock()
        mock_store.search_materials.return_value = []
        mock_store.add_material.return_value = 88
        mock_history = MagicMock()

        register_tools(mock_mcp, mock_store, mock_history)

        # Check all 4 tools are registered
        self.assertIn("save_vocabulary_flashcard", registered)
        self.assertIn("quiz_language_vocabulary", registered)
        self.assertIn("start_conversation_roleplay", registered)
        self.assertIn("explain_grammar_and_nuance", registered)

        token = mcp_active_owner_ctx.set(self.owner_id)
        try:
            # 1. Test save tool
            save_fn = registered["save_vocabulary_flashcard"]
            res = save_fn(
                word="Appreciate",
                language="Inggris",
                translation="Menghargai",
                level="A2",
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["material_id"], 88)

            # 2. Test roleplay tool
            rp_fn = registered["start_conversation_roleplay"]
            rp_res = rp_fn(scenario="bandara", target_language="Inggris", level="Pemula", turn_number=1)
            self.assertTrue(rp_res["success"])
            self.assertIn("passport", rp_res["kalimat_pembuka_target"].lower())

            # 3. Test grammar tool
            gram_fn = registered["explain_grammar_and_nuance"]
            gram_res = gram_fn(topic="four tones mandarin", target_language="Mandarin")
            self.assertTrue(gram_res["success"])
        finally:
            mcp_active_owner_ctx.reset(token)


if __name__ == "__main__":
    unittest.main()
