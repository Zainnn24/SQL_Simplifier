from dataclasses import dataclass
from time import perf_counter


E2_SCRIPTED_MODEL_ID = "scripted-e2"
E2_COPY_FIRST_MODEL_ID = "scripted-e2-copy-first"

_E2_RESPONSES = (
    "SELECT row_id FROM jobs WHERE end_day >= 10",
    "SELECT row_id FROM jobs WHERE start_day >= 10",
)


@dataclass(frozen=True)
class ModelResponse:
    raw_response: str
    model_id: str
    input_tokens: int | None
    output_tokens: int | None
    elapsed_ms: float


class ScriptedModel:
    """Return the predefined answers for one scripted run."""

    def __init__(
        self,
        responses: tuple[str, ...] = _E2_RESPONSES,
        *,
        model_id: str = E2_SCRIPTED_MODEL_ID,
    ) -> None:
        self._responses = iter(responses)
        self._model_id = model_id
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> ModelResponse:
        started_at = perf_counter()
        raw_response = next(self._responses, None)
        if raw_response is None:
            raise RuntimeError("The E2-01 scripted model has no responses left.")

        self.prompts.append(prompt)
        return ModelResponse(
            raw_response=raw_response,
            model_id=self._model_id,
            input_tokens=0,
            output_tokens=0,
            elapsed_ms=(perf_counter() - started_at) * 1000,
        )
