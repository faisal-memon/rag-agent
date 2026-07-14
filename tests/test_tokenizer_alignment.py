import os
import unittest


class TokenizerAlignmentTest(unittest.TestCase):
    def test_mxbai_tokenizer_diagnostic_counts(self) -> None:
        try:
            import tiktoken
        except ModuleNotFoundError as exc:
            self.skipTest(f"tiktoken is not installed: {exc}")

        try:
            from transformers import AutoTokenizer
        except ModuleNotFoundError as exc:
            self.skipTest(f"transformers is not installed: {exc}")

        model_id = os.getenv("EMBEDDING_TOKENIZER_MODEL_ID", "mixedbread-ai/mxbai-embed-large-v1")
        allow_download = os.getenv("ALLOW_TOKENIZER_DOWNLOAD", "").lower() in {"1", "true", "yes"}
        try:
            tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=not allow_download)
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest(f"{model_id} tokenizer is not available locally: {exc}")

        try:
            encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:
            self.skipTest(f"tiktoken cl100k_base encoding is not available locally: {exc}")
        samples = [
            "[Doc: example.pdf]\n\nHello, world",
            (
                "[Doc: sample-property-deed.pdf]\n[Page: 1]\n\n"
                "THIS GRANT DEED, executed on November 3, 2021, transfers the property located at "
                "123 Example Street, Example City, Example State, to the grantee."
            ),
            (
                "[Doc: 105592378-singleSigner.pdf]\n[Page: 7]\n\n"
                "Borrower certifies employment status, year-to-date income, obligations, escrow amounts, "
                "and acknowledges lender disclosures for the August 2024 loan package."
            ),
        ]

        saw_difference = False
        for sample in samples:
            with self.subTest(sample=sample[:48]):
                tiktoken_count = len(encoder.encode(sample))
                model_count = len(tokenizer.encode(sample, add_special_tokens=False))
                print(
                    f"\nSample: {sample[:60]!r}... "
                    f"tiktoken={tiktoken_count} mxbai_tokenizer={model_count}"
                )
                self.assertGreater(tiktoken_count, 0)
                self.assertGreater(model_count, 0)
                if tiktoken_count != model_count:
                    saw_difference = True

        self.assertTrue(
            saw_difference,
            "Expected at least one sample to tokenize differently between tiktoken and the mxbai tokenizer.",
        )


if __name__ == "__main__":
    unittest.main()
