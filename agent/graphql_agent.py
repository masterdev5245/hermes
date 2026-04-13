import os
import sys

# Add the parent directory (agent) to sys.path so we can import subquery_graphql_agent as a package  
agent_dir = os.path.dirname(__file__)
if agent_dir not in sys.path:
    sys.path.insert(0, agent_dir)

# Import directly now that directory name doesn't have hyphens
from subquery_graphql_agent.base import (
    GraphQLAgent,
    ProjectConfig
)

def initServerAgentWithConfig(project_config: ProjectConfig) -> GraphQLAgent:
    """
    Create a GraphQLAgent with a pre-configured ProjectConfig.
    """
    agent = GraphQLAgent(project_config)
    return agent

# def initExampleAgent() -> ExampleGraphQLAgent:
#     agent = ExampleGraphQLAgent("https://index-api.onfinality.io/sq/subquery/subquery-mainnet")
#     return agent