async def _strip_question(question: str) -> str:
    """Strip surrounding whitespace natively asynchronously, without an executor."""
    return question.strip()


async def normalize_question(question: str) -> str:
    """Invoke the real async Core runnable, without retrieval or generation."""
    # Import after composition disables tracing; native import errors stay visible.
    from langchain_core.runnables import RunnableLambda

    return await RunnableLambda(_strip_question).ainvoke(
        question, config={"callbacks": []}
    )
