import asyncio
import os
from pathlib import Path
import dotenv
from langchain_openai import ChatOpenAI
from loguru import logger
from common.project_manager import ProjectManager
from hermes.validator.question_generator import QuestionGenerator
dotenv.load_dotenv('.env.validator')


SUBQL_CID_HASH = 'QmfUNJC1Qz8m3F67sQmxrwjuSAu4WaCR1iBdPPdzBruQ7P_00021a18'
model_name = os.getenv("LLM_MODEL")


# python -m scripts.synthetic_generate
if __name__ == "__main__":
    target_dir = Path(__file__).parent.parent / "projects" / "validator"
    logger.info(f"Loading projects from {target_dir}")

    pm = ProjectManager(llm=None, target_dir=target_dir)
    projects_config = pm.load()

    logger.info(f"Loaded {len(projects_config)} projects")
    p = projects_config.get(SUBQL_CID_HASH)
    llm = ChatOpenAI(
        model=model_name,
        temperature=1
    )

    count = 10
    question_generator = QuestionGenerator(max_history=count)

    logger.info(f"model_name: {model_name}")
    logger.info(f"entity_schema: ({len(p.schema_content)} chars)")

    for i in range(10):
        question = asyncio.run(question_generator.generate_question(SUBQL_CID_HASH, p.schema_content, llm))
        logger.info(f"Generated question {i+1}/{count}: {question}")

        logger.info(f"\n")



