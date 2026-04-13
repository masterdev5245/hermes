import aiohttp
from typing import Any, Dict

async def request_subquery(options: Dict[str, Any]):
    async with aiohttp.ClientSession() as session:
        payload = {
            "variables": options.get("variables", {}),
             "query": options["query"]
        }
        url = options.get("url") or "https://index-api.onfinality.io/sq/subquery/subquery-mainnet"
        timeout = options.get("timeout", 30)
        method = options.get("method", "POST").upper()

        async with session.request(
            method,
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            result = await resp.json()
            res = result.get("data", {}).get(options["type"])
        return res

async def query_indexer_rewards(indexer: str, era: str, block_height: str = "") -> int:
    """
    Query the total rewards for a specific indexer in a given era.

    Do NOT call this tool when:
        1. The query is related to Stake, APY, Commission Rate or other non-reward metrics.

    Args:
        indexer (str): The indexer address or identifier.
        era (str): The era number. Supports two formats:
            - Hexadecimal, e.g. "0x48"
            - Decimal, e.g. "72" (equivalent to 0x48)
        block_height (str): Specific block height to query the data at. You MUST pass this parameter in these cases:
            - If user's question explicitly mentions a specific block height, use that value
            - If CURRENT BLOCK HEIGHT (from system message) is NOT 0, you MUST pass it here
            - Only leave empty ("") if CURRENT BLOCK HEIGHT is 0 or not provided

    Returns:
        int: Total rewards earned by the indexer in the specified era,
             returned in 18-decimal precision SQT (wei units).

    Examples:
        >>> # When CURRENT BLOCK HEIGHT = 38120187 (non-zero)
        >>> await query_indexer_rewards("indexer_address", "0x48", "38120187")  # MUST include block_height
        >>> 
        >>> # When user asks about specific block
        >>> await query_indexer_rewards("indexer_address", "72", "5000000")  # Use user's block height
        >>> 
        >>> # Only when CURRENT BLOCK HEIGHT is 0
        >>> await query_indexer_rewards("indexer_address", "0x48")  # Can omit block_height
    """

    query = '''
    query (
      $id: String!
      $blockHeight: String!
    ) {
      indexerReward(
        id: $id
        blockHeight: $blockHeight
      ) {
        id
        amount
      }
    }
    '''

    if era.startswith("0x"):
        era_hex = era.lower()
    else:
        try:
            era_hex = hex(int(era))
        except Exception:
            era_hex = era

    print("-----------indexer:", indexer, " era_hex:", era_hex, " block_height:", block_height)
    r = await request_subquery({
        "query": query,
        "type": "indexerReward",
        "variables": {
            "id": f"{indexer}:{era_hex}",
            "blockHeight": block_height
        },
    })
    return r.get('amount') if r else 0

tools = [query_indexer_rewards]