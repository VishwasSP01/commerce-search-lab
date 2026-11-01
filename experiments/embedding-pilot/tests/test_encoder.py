import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import BertConfig, BertModel, BertTokenizerFast
from encoder import E5Encoder, masked_mean, prepare_texts


class EncoderTests(unittest.TestCase):
    def test_masked_mean_ignores_padding_and_rejects_empty_mask(self):
        hidden = torch.tensor([[[2., 4.], [4., 8.], [1000., 1000.]]])
        self.assertTrue(torch.equal(masked_mean(hidden, torch.tensor([[1, 1, 0]])), torch.tensor([[3., 6.]])))
        with self.assertRaises(ValueError):
            masked_mean(hidden, torch.tensor([[0, 0, 0]]))

    def test_prefixes_and_nfkc_apply_to_all_languages(self):
        self.assertEqual(prepare_texts(["Ａ shoes", "靴", "zapato"], "passage"),
                         ["passage: A shoes", "passage: 靴", "passage: zapato"])
        self.assertEqual(prepare_texts(["靴"], "query"), ["query: 靴"])
        with self.assertRaises(ValueError):
            prepare_texts(["shoe"], "unknown")

    def test_local_safetensors_encoder_shape_norm_padding_and_truncation(self):
        torch.manual_seed(42)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "passage", "query", ":", "shoe", "red", "hat", "靴"]
            (root / "vocab.txt").write_text("\n".join(vocab) + "\n")
            tokenizer = BertTokenizerFast(vocab_file=str(root / "vocab.txt"), do_lower_case=False)
            tokenizer.save_pretrained(root)
            BertModel(BertConfig(vocab_size=len(vocab), hidden_size=8, num_hidden_layers=1,
                                 num_attention_heads=2, intermediate_size=16)).save_pretrained(root)
            policy = {"cpu_threads": 1, "max_length": 12, "embedding_dimensions": 8}
            encoder = E5Encoder(root, policy, device="cpu")
            vector = encoder.encode(["shoe"])
            batch = encoder.encode(["shoe", "red " * 20])
            self.assertEqual(batch.shape, (2, 8))
            self.assertEqual(batch.dtype, np.dtype("float32"))
            self.assertTrue(np.isfinite(batch).all())
            np.testing.assert_allclose(np.linalg.norm(batch, axis=1), 1, atol=1e-6)
            np.testing.assert_allclose(vector[0], batch[0], atol=1e-6)
            self.assertGreater(encoder.lengths(["red " * 20])[0], policy["max_length"])
            self.assertEqual(encoder.encode(["靴"], "query").shape, (1, 8))
            with self.assertRaises(ValueError):
                E5Encoder(root, dict(policy, embedding_dimensions=384), device="cpu")


if __name__ == "__main__":
    unittest.main()
