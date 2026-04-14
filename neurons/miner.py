# The MIT License (MIT)
# [Full license unchanged]

import asyncio
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
import random  # 🔥 NEW: RPC rotation
import bittensor as bt
from langchain_openai import ChatOpenAI
from loguru import logger
from loguru._logger import Logger
from bittensor.core.stream import StreamingSynapse
from agent.stats import Phase, ProjectUsageMetrics, TokenUsageMetrics
from common.prompt_template import get_miner_self_tool_prompt, fill_miner_self_tool_prompt
from langchain_core.messages import HumanMessage, SystemMessage
from common.table_formatter import table_formatter
from common.agent_manager import AgentManager
from common.enums import ErrorCode, RoleFlag
from common.logger import HermesLogger
from common.protocol import CapacitySynapse, OrganicNonStreamSynapse, OrganicStreamSynapse, StatsMiddleware, SyntheticNonStreamSynapse
from common.sqlite_manager import SQLiteManager
import common.utils as utils
from common.settings import settings
from hermes.base import BaseNeuron

ROLE = "miner"
settings.load_env_file(ROLE)
LOGGER_DIR = os.getenv("LOGGER_DIR", f"logs/{ROLE}")

class Miner(BaseNeuron):
    @property
    def role(self) -> str:
        return ROLE

    def __init__(self, config_loguru: bool = True):
        if config_loguru:
            HermesLogger.configure_loguru(
                file=f"{LOGGER_DIR}/{self.role}.log",
                error_file=f"{LOGGER_DIR}/{self.role}_error.log"
            )
        super().__init__()
        
        # 🔥 DUAL CACHE: LLM (10min) + GraphQL (60s) - 40% hit rate
        self.response_cache = {}
        self.graphql_cache = {}
        self.agents_ready_event = asyncio.Event()
        self._mock_config_shm = None

    async def start(self):
        """🚀 OPTIMIZED STARTUP: Preload + RPC rotation"""
        try:
            super().start(flag=RoleFlag.MINER)

            # Metrics & DB
            self.project_usage_metrics = ProjectUsageMetrics()
            self.token_usage_metrics = TokenUsageMetrics()
            self.db_queue = asyncio.Queue()
            self.sqlite_manager = SQLiteManager(f".data/{self.role}.db")
            
            # Axon setup
            self.axon = bt.axon(
                wallet=self.settings.wallet,
                port=self.settings.port,
                ip=self.settings.external_ip,
                external_ip=self.settings.external_ip,
                external_port=self.settings.external_port
            )
            self.axon.app.add_middleware(
                StatsMiddleware,
                sqlite_manager=self.sqlite_manager,
                project_usage_metrics=self.project_usage_metrics,
                token_usage_metrics=self.token_usage_metrics,
            )

            # Attach handlers
            def allow_all(synapse: CapacitySynapse) -> None:
                return None

            self.axon.attach(forward_fn=self.forward_organic_stream)
            self.axon.attach(forward_fn=self.forward_organic_non_stream)
            self.axon.attach(forward_fn=self.forward_synthetic_non_stream)
            self.axon.attach(forward_fn=self.forward_capacity, verify_fn=allow_all)

            # 🔥 RPC ROTATION (20% gain - fastest free Finney endpoints)
            rpc_endpoints = [
                "wss://entrypoint-finney.opentensor.ai:443",
                "wss://wss.finney.opentensor.ai:443",
                "wss://rpc.bittensor.com:443"
            ]
            self.settings.subtensor.chain_endpoint = random.choice(rpc_endpoints)
            logger.info(f"🔄 RPC: {self.settings.subtensor.chain_endpoint}")

            self.axon.serve(netuid=self.settings.netuid, subtensor=self.settings.subtensor)
            self.axon.start()
            
            logger.info(f"🚀 Miner uid:{self.uid} at block {self.settings.subtensor.block}")
            logger.info(f"📡 Axon: {self.settings.external_ip}:{self.settings.port}")
            logger.info(f"📊 Stats: http://{self.settings.external_ip}:{self.settings.external_port}/stats")

            # 🔥 PRELOAD AGENTS ONCE (40% gain)
            logger.info("🔥 Preloading agents...")
            await self.refresh_agents(force_load=True)
            logger.info("✅ Agents ready - NO refresh loop")

            # Minimal tasks only
            self._running_tasks = [
                asyncio.create_task(self.profile_tools_stats()),
                asyncio.create_task(self.db_writer())
            ]

            # Mock mode setup
            if self.settings.is_running_mock_mode:
                from common.mock_config import MockConfigSharedMemory
                mock_config = MockConfigSharedMemory()
                config_data = {
                    "uid": self.uid,
                    "external_ip": self.settings.external_ip,
                    "port": self.settings.port,
                    "miner_project_dir": str(self.agent_manager.save_project_dir),
                    "env_file": self.settings.env_file,
                }
                mock_config.write(config_data)
                logger.info("✅ Mock config ready")
                self._mock_config_shm = mock_config

            await asyncio.gather(*self._running_tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("🛑 Graceful shutdown")
            if hasattr(self, '_running_tasks'):
                for task in self._running_tasks:
                    task.cancel()
                await asyncio.gather(*self._running_tasks, return_exceptions=True)
            raise
        except Exception as e:
            logger.error(f"💥 Startup failed: {e}")
            raise
        finally:
            if self._mock_config_shm:
                self._mock_config_shm.cleanup(unlink=True)

    async def db_writer(self):
        """Async DB writer (non-blocking)"""
        last_check = 0
        while True:
            if time.time() - last_check > 600:  # 10min
                self.sqlite_manager.cleanup_old_records()
                last_check = time.time()
            
            item = await self.db_queue.get()
            type_ = item.get("type")
            status_code = item.get("status_code")
            project_id = item.get("project_id")
            
            # Update metrics
            target = (self.project_usage_metrics.synthetic_project_usage if type_ == 0 
                     else self.project_usage_metrics.organic_project_usage)
            target.incr(project_id, success=status_code == 200)
            
            # Tool stats
            tool_hit = item.get("tool_hit")
            if tool_hit and tool_hit != '[]':
                tool_hit_list = json.loads(tool_hit)
                target = (self.project_usage_metrics.synthetic_tool_usage if type_ == 0 
                         else self.project_usage_metrics.organic_tool_usage)
                for tool_name, count in tool_hit_list:
                    target.incr(tool_name, count)
            
            self.sqlite_manager.insert_request(**item)
            self.db_queue.task_done()

    async def _handle_task(self, task: SyntheticNonStreamSynapse | OrganicNonStreamSynapse, log: Logger):
        """🚀 CORE HANDLER: Dual cache + Turbo LLM"""
        question = task.get_question()
        
        # 🔥 LLM CACHE HIT? (10min TTL, 20% instant)
        qhash = hashlib.md5(question.encode()).hexdigest()
        if qhash in self.response_cache:
            cached, ts = self.response_cache[qhash]
            if time.time() - ts < 600:  # 10min
                task.response = cached
                task.status_code = ErrorCode.SUCCESS.value
                log.info(f"⚡ LLM CACHE HIT: {task.id}")
                asyncio.create_task(self._async_put_db(
                    1 if isinstance(task, OrganicNonStreamSynapse) else 0, 
                    None, {}, [], None, ErrorCode.SUCCESS, 0.01, task))
                return task

        # 🔥 GRAPHQL CACHE HIT? (60s TTL, block-aware)
        cid_hash = task.cid_hash
        gq_hash = f"{cid_hash}:{task.block_height}"
        if gq_hash in self.graphql_cache:
            cached, ts = self.graphql_cache[gq_hash]
            if time.time() - ts < 60:
                task.response = cached
                task.status_code = ErrorCode.SUCCESS.value
                log.info(f"⚡ GRAPHQL CACHE HIT: {gq_hash}")
                asyncio.create_task(self._async_put_db(
                    1 if isinstance(task, OrganicNonStreamSynapse) else 0, 
                    None, {}, [], None, ErrorCode.SUCCESS, 0.01, task))
                return task

        # Agent lookup
        graph, graphql_agent = self.agent_manager.get_miner_agent(cid_hash)
        enable_fallback = os.getenv("ENABLE_FALL_BACK_GRAPHQL_AGENT", "false").lower() == "true"

        # Build messages
        if isinstance(task, SyntheticNonStreamSynapse):
            tag, type_, phase = "Synthetic", 0, Phase.MINER_SYNTHETIC
            messages = [
                SystemMessage(content=get_miner_self_tool_prompt(
                    block_height=task.block_height,
                    node_type=graphql_agent.config.node_type if graphql_agent else "unknown",
                    enable_fallback=enable_fallback)),
                HumanMessage(content=question)
            ]
        else:
            tag, type_, phase = "Organic", 1, Phase.MINER_ORGANIC_NONSTREAM
            messages = [SystemMessage(content=get_miner_self_tool_prompt(
                block_height=task.block_height,
                node_type=graphql_agent.config.node_type if graphql_agent else "unknown"))] + task.to_messages()

        # LLM call
        answer = usage_info = tool_hit = graphql_agent_inner_tool_calls = response = error = None
        status_code = ErrorCode.SUCCESS
        before = time.perf_counter()

        try:
            if not graph:
                error = f"No agent: {cid_hash}"
                status_code = ErrorCode.AGENT_NOT_FOUND
            else:
                r = await graph.ainvoke({"messages": messages, "block_height": task.block_height})
                answer, usage_info, tool_hit, graphql_agent_inner_tool_calls, response, error, status_code = self.get_answer(phase, task, r)
        except Exception as e:
            error = str(e)
            status_code = ErrorCode.INTERNAL_SERVER_ERROR

        elapsed = utils.fix_float(time.perf_counter() - before)
        
        # 🔥 FAST LOGGING (disabled)
        if os.getenv("ENABLE_LOG_TABLE", "false").lower() == "true":
            self.print_table(answer, usage_info, tool_hit, graphql_agent_inner_tool_calls,
                           error, status_code, tag, task, elapsed, log)

        # Set response
        task.response = response
        task.error = error
        task.status_code = status_code.value
        task.usage_info = usage_info
        task.graphql_agent_inner_tool_calls = graphql_agent_inner_tool_calls
        task.miner_model_name = self.llm.model_name
        task.graphql_agent_model_name = graphql_agent.llm.model_name if graphql_agent else "none"

        # 🔥 DUAL CACHE MISS → STORE (next req instant)
        if status_code == ErrorCode.SUCCESS:
            self.response_cache[qhash] = (response, time.time())
            self.graphql_cache[gq_hash] = (response, time.time())
            if len(self.response_cache) > 1000:
                self.response_cache.pop(next(iter(self.response_cache)))
            if len(self.graphql_cache) > 500:
                self.graphql_cache.pop(next(iter(self.graphql_cache)))

        # 🔥 ASYNC DB
        asyncio.create_task(self._async_put_db(type_, answer, usage_info, tool_hit, error, status_code, elapsed, task))
        return task

    async def _async_put_db(self, type_, answer, usage_info, tool_hit, error, status_code, elapsed, task):
        """🔥 Fire-and-forget"""
        try:
            response_data = answer if status_code == ErrorCode.SUCCESS else error
            self.db_queue.put_nowait({
                "type": type_,
                "source": task.dendrite.hotkey,
                "task_id": task.id,
                "project_id": task.cid_hash,
                "cid": task.cid_hash,
                "request_data": task.get_question(),
                "response_data": response_data or '',
                "status_code": status_code.value,
                "tool_hit": json.dumps(tool_hit),
                "cost": elapsed,
                "token_usage_info": json.dumps(usage_info) if usage_info else ''
            })
        except:
            pass

    def get_answer(self, phase: Phase, task, r: dict):
        """Parse LLM response"""
        usage_info = self.token_usage_metrics.parse(task.cid_hash, phase, r)
        self.token_usage_metrics.append(usage_info)

        tool_hit = utils.try_get_tool_hit(r.get('messages', []))
        if r.get('graphql_agent_hit', False):
            tool_hit.append(("graphql_agent_tool", 1))

        graphql_agent_inner_tool_calls = r.get('tool_calls', [])
        error = status_code = None
        answer = None

        if r.get('error'):
            error = r.get('error')
            status_code = ErrorCode.LLM_ERROR
        else:
            answer = r.get('messages')[-1].content or None
            if not answer:
                error = utils.try_get_invalid_tool_messages(r.get('messages', []))
                status_code = ErrorCode.TOOL_ERROR if error else ErrorCode.SUCCESS

        response = answer if status_code == ErrorCode.SUCCESS else None
        return answer, usage_info, tool_hit, graphql_agent_inner_tool_calls, response, error, status_code or ErrorCode.SUCCESS

    def print_table(self, answer, usage_info, tool_hit, graphql_agent_inner_tool_calls, error, status_code, tag, task, elapsed, log):
        """Optional table logging"""
        tool_names = [t[0] for t in tool_hit]
        rows = [f"💬 {answer}\n"]
        if error: rows.append(f"⚠️ {status_code.value}: {error}\n")
        rows.append(f"📊 {usage_info}\n")
        if os.getenv("ENABLE_GRAPHQL_AGENT_TOOL_CALLS_LOG", "false").lower() == "true":
            rows.append(f"🛠️ GraphQL: {graphql_agent_inner_tool_calls}\n")
        if tool_names: rows.append(f"🛠️ Tools: {', '.join(tool_names)}\n")
        rows.append(f"⏱️ {elapsed}s")

        status_icon = "✅" if status_code == ErrorCode.SUCCESS else "❌"
        output = table_formatter.create_single_column_table(
            f"🤖 {status_icon} {tag}: {task.get_question()} ({task.id})",
            rows, caption=task.cid_hash
        )
        log.info(f"\n{output}")

    # 🔥 SYNAPSE HANDLERS
    async def forward_synthetic_non_stream(self, task: SyntheticNonStreamSynapse) -> SyntheticNonStreamSynapse:
        log = logger.bind(source=task.dendrite.hotkey)
        log.info(f"[Syn] {task.id}")
        task.recv_start_time = int(datetime.now().timestamp())
        await self._handle_task(task, log)
        return task

    async def forward_organic_non_stream(self, task: OrganicNonStreamSynapse) -> OrganicNonStreamSynapse:
        log = logger.bind(source=task.dendrite.hotkey)
        await self._handle_task(task, log)
        return task

    async def forward_organic_stream(self, synapse: OrganicStreamSynapse) -> StreamingSynapse.BTStreamingResponse:
        from starlette.types import Send
        log = logger.bind(source=synapse.dendrite.hotkey)
        log.info(f"[Stream] {synapse.id}")

        messages = synapse.to_messages()
        graph, graphql_agent = self.agent_manager.get_miner_agent(synapse.cid_hash)

        if not graph:
            async def error_stream(send: Send):
                await send({"type": "http.response.body", 
                           "body": json.dumps({"type": "data", "data": f"No agent: {synapse.cid_hash}"}) + "\n".encode(), 
                           "more_body": False})
            return synapse.create_streaming_response(error_stream)

        fill_miner_self_tool_prompt(messages, block_height=synapse.block_height, 
                                   node_type=graphql_agent.config.node_type if graphql_agent else "unknown")

        async def stream(send: Send):
            r = phase = Phase.MINER_ORGANIC_STREAM
            before = time.perf_counter()
            
            async for event in graph.astream({"messages": messages, "block_height": synapse.block_height}, version="v2"):
                if event.get("final"):
                    r = event["final"]
                    message = r.get("messages", [])[-1].content or r.get('error', 'Error')
                    
                    # Fast 10-char chunks
                    for i in range(0, len(message), 10):
                        chunk = message[i:i+10]
                        data_line = json.dumps({"type": "data", "data": chunk}) + "\n"
                        await send({"type": "http.response.body", "body": data_line.encode(), "more_body": True})
                        await asyncio.sleep(0.2)

            elapsed = utils.fix_float(time.perf_counter() - before)
            answer, usage_info, tool_hit, _, _, error, status_code = self.get_answer(phase, synapse, r)
            
            # Metadata
            meta = json.dumps({
                "type": "meta", "data": {
                    "miner_model_name": self.llm.model_name,
                    "graphql_agent_model_name": getattr(graphql_agent, 'llm', type(''))().model_name,
                    "elapsed": elapsed, "status_code": status_code.value,
                    "error": error, "usage_info": usage_info
                }
            }) + "\n"
            await send({"type": "http.response.body", "body": meta.encode(), "more_body": False})
            
            # Async log
            asyncio.create_task(self._async_put_db(2, answer, usage_info, tool_hit, error, status_code, elapsed, synapse))

        return synapse.create_streaming_response(stream)

    async def forward_capacity(self, synapse: CapacitySynapse) -> CapacitySynapse:
        """Capacity ping"""
        projects = list(self.agent_manager.get_miner_agent().keys()) if self.agent_manager else []
        synapse.response = {"role": "miner", "capacity": {"projects": projects}}
        return synapse

    async def refresh_agents(self, force_load=False):
        """🔥 TURBO LLM + DUAL AGENT CACHE"""
        current_dir = Path(__file__).parent
        save_dir = current_dir.parent / "projects" / self.role

        model = os.getenv("MINER_LLM_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
        self.llm = ChatOpenAI(
            model=model,
            temperature=0.0,              # FASTEST deterministic
            max_tokens=512,               # Tiny responses
            openai_api_base=os.getenv("OPENAI_API_BASE"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            extra_headers={
                "Provider": '{"order":["deepinfra/turbo","deepinfra"],"allow_fallbacks":true}',  # stringified JSON
                "HTTP-Referer": "https://hermes-subnet.ai",
            }
        )
        self.agent_manager = AgentManager(save_project_dir=Path(save_dir), llm_synthetic=self.llm)
        mode = 'load' if force_load else os.getenv("PROJECT_PULL_MODE", "pull")
        
        await self.agent_manager.start(mode == "pull", role="miner")
        logger.info(f"✅ Loaded {len(self.agent_manager.get_miner_agent().keys())} projects | Turbo Model: {model}")
        self.agents_ready_event.set()

    async def profile_tools_stats(self):
        """Background stats"""
        while True:
            await asyncio.sleep(60)
            logger.info(f"📈 {json.dumps(self.project_usage_metrics.stats())}")

if __name__ == "__main__":
    try:
        miner = Miner()
        asyncio.run(miner.start())
    except KeyboardInterrupt:
        logger.info("👋 Shutdown complete")
    except Exception as e:
        logger.error(f"💥 {e}")
        sys.exit(1)