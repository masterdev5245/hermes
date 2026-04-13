import asyncio
from loguru import logger
import itertools

from common.protocol import SyntheticNonStreamSynapse
from common.timer import Timer
from hermes.validator.scorer_manager import ScorerManager
from neurons.miner import Miner

async def show_progress(msg: str = '', interval=0.5):
    for dots in itertools.cycle([".", "..", "...", "...."]):
        print(f"\r{msg}{dots} ", end="", flush=True)
        await asyncio.sleep(interval)

async def main():
    replay_count = 1
    miner = Miner(config_loguru=False)

    await miner.refresh_agents(force_load=True)
    scorer_manager = ScorerManager(llm_score=miner.llm)

    local_agents = miner.agent_manager.get_miner_agent()

    while True:
        try:
            local_projects_cids = list(local_agents.keys())
            print("\nAvailable projects:")
            for idx, cid in enumerate(local_projects_cids, start=1):
                print(f"{idx}) {cid}")

            selected_index = input("\nplease select a project: ").strip()
            if not selected_index.isdigit() or int(selected_index) < 1 or int(selected_index) > len(local_projects_cids):
                print("❌ Invalid selection. Please try again.")
                continue

            selected_cid = local_projects_cids[int(selected_index) - 1]
            print(f"\n✅ you selected: {selected_cid}")

            while True:
                question = input("\n🙋 input replay challenge: ").strip()
                if question.lower() in ['quit', 'exit', 'q']:
                    print("👋 Goodbye!")
                    return
                if not question:
                    print("❌ Question cannot be empty. Please try again.")
                    continue
                break
            
            replay_id = f"replay-{replay_count:03d}"
            synapse = SyntheticNonStreamSynapse(id=replay_id, project_id=selected_cid, question=question)

            progress_task = asyncio.create_task(show_progress("generate reference ground truth", 0.5))
            with Timer() as t:
                ground_truth = await miner.invoke_graphql_agent(synapse)
            progress_task.cancel()
            print(f"\r🤖 reference ground truth: {ground_truth}\n")
            ground_truth_cost = t.final_time

            progress_task = asyncio.create_task(show_progress("generate miner answer", 0.5))
            with Timer() as t:
                miner_answer = await miner.invoke_miner_agent(synapse)
            progress_task.cancel()
            print(f"\r🤖 miner answer: {miner_answer}\n")
            synapse.response = miner_answer
            synapse.elapsed_time =  t.final_time

            zip_scores, _, _ = await scorer_manager.compute_challenge_score(
                ground_truth, 
                ground_truth_cost, 
                [synapse],
                challenge_id=replay_id
            )
            print(f"🏆 reference score: {zip_scores[0]}\n")

            replay_count += 1
            input(f"\n press enter to continue...\n")

        except KeyboardInterrupt:
            logger.info("\n👋 Goodbye!")
            break
        except Exception as e:
            logger.error(f"❌ Error: {e}")


# python -m cli.replay
if __name__ == "__main__":
    asyncio.run(main())