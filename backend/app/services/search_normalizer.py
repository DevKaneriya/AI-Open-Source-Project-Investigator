import re


class SearchNormalizer:
    @staticmethod
    def normalize(value: str) -> str:
        value = value.strip().lower()

        value = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1 \2",
            value,
        )

        value = re.sub(
            r"[_\-]+",
            " ",
            value,
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()

    @staticmethod
    def tokens(value: str) -> list[str]:
        normalized = SearchNormalizer.normalize(value)

        if not normalized:
            return []

        return normalized.split()