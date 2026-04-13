"""
GraphQL Schema Processing Module

Provides flexible GraphQL schema processing capabilities, supporting type extraction and SDL generation by query fields and depth.
"""

from typing import Dict, List, Set, Optional
import aiohttp
from graphql import get_introspection_query


async def fetch_graphql_schema(endpoint: str, include_arg_descriptions: bool = True, headers: Optional[Dict[str, str]] = None) -> Dict:
    """
    Fetch schema information from GraphQL endpoint
    
    Args:
        endpoint: GraphQL endpoint URL
        include_arg_descriptions: Whether to include argument description info, if False will filter out args descriptions
        headers: Optional HTTP headers to include in the request
        
    Returns:
        Dict: JSON representation of the schema
    """
    # Use the standard introspection query from graphql-core
    introspection_query = get_introspection_query(descriptions=include_arg_descriptions)
    
    # Prepare headers
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            endpoint,
            json={"query": introspection_query},
            headers=request_headers
        ) as response:
            if response.status == 200:
                data = await response.json()
                
                # Return the complete introspection result for graphql-core compatibility
                return data
            else:
                raise Exception(f"Failed to fetch schema: {response.status}")


def _process_query_filter_mode(query_filter: str, query_type: Dict, type_lookup: Dict, depth: int, builtin_scalars: set, builtin_types: set, include_arg_descriptions: bool) -> str:
    """
    Process query_filter mode logic
    
    Args:
        query_filter: Query field name
        query_type: Query type definition
        type_lookup: Type lookup table
        depth: Expansion depth
        builtin_scalars: Built-in scalar types set
        builtin_types: Built-in types set
        include_arg_descriptions: Whether to include argument descriptions
        
    Returns:
        str: Generated SDL format string
    """
    # Filter Query fields
    query_fields = [f for f in query_type.get("fields", []) if f.get("name") == query_filter]
    if not query_fields:
        return f"# Query field '{query_filter}' not found"
    
    # Generate SDL parts
    sdl_parts = []
    
    # 1. Generate Query type definition (required for depth 0+)
    query_fields_sdl = []
    for field in query_fields:
        field_name = field.get("name", "")
        field_type = format_type(field.get("type", {}))
        field_desc = field.get("description")
        
        if field_desc:
            query_fields_sdl.append(f'  """{field_desc}"""')
        
        # Process field arguments
        args = field.get("args", [])
        if args:
            args_str = []
            for arg in args:
                arg_name = arg.get("name", "")
                arg_type = format_type(arg.get("type", {}))
                arg_desc = arg.get("description")
                
                # Decide whether to include descriptions based on include_arg_descriptions parameter
                if include_arg_descriptions and arg_desc:
                    args_str.append(f'{arg_name}: {arg_type} # {arg_desc}')
                else:
                    args_str.append(f'{arg_name}: {arg_type}')
            query_fields_sdl.append(f'  {field_name}({", ".join(args_str)}): {field_type}')
        else:
            query_fields_sdl.append(f'  {field_name}: {field_type}')
    
    # Build Query SDL
    query_sdl = f'""" Query field: {query_filter} """\ntype Query {{\n' + '\n'.join(query_fields_sdl) + '\n}\n'
    sdl_parts.append(query_sdl)
    
    # 2. If depth is 0, only return Query itself
    if depth == 0:
        return '\n\n'.join(sdl_parts)
    
    # 3. Depth >= 1, need to extract related types
    collected_types = set()
    types_to_process = []
    
    # Extract initial types from Query fields
    for field in query_fields:
        field_types = extract_referenced_types_from_type({"fields": [field]})
        for type_name in field_types:
            if (type_name not in builtin_scalars and 
                type_name not in builtin_types and 
                not type_name.startswith("__")):
                types_to_process.append((type_name, 1))  # (type_name, current_depth)
    
    # Recursively process types (by depth)
    current_depth = 1
    while types_to_process and current_depth <= depth:
        next_batch = []
        
        for type_name, type_depth in types_to_process:
            if type_name in collected_types or type_depth > depth:
                continue
            
            collected_types.add(type_name)
            
            if type_name in type_lookup:
                # If current depth is less than target depth, continue expanding
                if type_depth < depth:
                    nested_types = extract_referenced_types_from_type(type_lookup[type_name])
                    for nested_type in nested_types:
                        if (nested_type not in builtin_scalars and 
                            nested_type not in builtin_types and 
                            not nested_type.startswith("__") and
                            nested_type not in collected_types):
                            next_batch.append((nested_type, type_depth + 1))
        
        types_to_process = next_batch
        current_depth += 1
    
    # 4. Generate SDL for collected types
    for type_name in sorted(collected_types):
        if type_name in type_lookup:
            type_sdl = convert_single_type_to_sdl(type_lookup[type_name])
            if type_sdl:
                sdl_parts.append(type_sdl)
    
    return '\n\n'.join(sdl_parts)


def _process_type_filter_mode(type_filter: str, type_lookup: Dict, depth: int, builtin_scalars: set, builtin_types: set) -> str:
    """
    Process type_filter mode logic
    
    Args:
        type_filter: Type name to expand
        type_lookup: Type lookup table
        depth: Expansion depth
        builtin_scalars: Built-in scalar types set
        builtin_types: Built-in types set
        
    Returns:
        str: Generated SDL format string
    """
    if type_filter not in type_lookup:
        return f"# Type '{type_filter}' not found in schema"
    
    target_type = type_lookup[type_filter]
    sdl_parts = []
    
    # 1. Add target type itself
    target_sdl = convert_single_type_to_sdl(target_type)
    if target_sdl:
        sdl_parts.append(f'""" Target type: {type_filter} """\n{target_sdl}')
    
    # 2. If depth is 0, only return target type itself
    if depth == 0:
        return '\n\n'.join(sdl_parts)
    
    # 3. Depth >= 1, need to extract related types
    collected_types = set()
    types_to_process = []
    
    # Extract initial types from target type
    initial_types = extract_referenced_types_from_type(target_type)
    for type_name in initial_types:
        if (type_name not in builtin_scalars and 
            type_name not in builtin_types and 
            not type_name.startswith("__") and
            type_name != type_filter):  # Avoid including self
            types_to_process.append((type_name, 1))  # (type_name, current_depth)
    
    # Recursively process types (by depth)
    current_depth = 1
    while types_to_process and current_depth <= depth:
        next_batch = []
        
        for type_name, type_depth in types_to_process:
            if type_name in collected_types or type_depth > depth:
                continue
            
            collected_types.add(type_name)
            
            if type_name in type_lookup:
                # If current depth is less than target depth, continue expanding
                if type_depth < depth:
                    nested_types = extract_referenced_types_from_type(type_lookup[type_name])
                    for nested_type in nested_types:
                        if (nested_type not in builtin_scalars and 
                            nested_type not in builtin_types and 
                            not nested_type.startswith("__") and
                            nested_type not in collected_types and
                            nested_type != type_filter):  # Avoid circular references
                            next_batch.append((nested_type, type_depth + 1))
        
        types_to_process = next_batch
        current_depth += 1
    
    # 4. Generate SDL for collected types
    for type_name in sorted(collected_types):
        if type_name in type_lookup:
            type_sdl = convert_single_type_to_sdl(type_lookup[type_name])
            if type_sdl:
                sdl_parts.append(type_sdl)
    
    return '\n\n'.join(sdl_parts)



def format_type(type_def: Dict) -> str:
    """
    Convert GraphQL type definition to SDL format type string
    
    Args:
        type_def: Type definition dictionary
        
    Returns:
        str: SDL format type string
    """
    if not type_def:
        return "Unknown"
    
    kind = type_def.get("kind", "")
    name = type_def.get("name")
    
    if kind == "NON_NULL":
        inner_type = format_type(type_def.get("ofType", {}))
        return f"{inner_type}!"
    elif kind == "LIST":
        inner_type = format_type(type_def.get("ofType", {}))
        return f"[{inner_type}]"
    elif name:
        return name
    else:
        return "Unknown"


def convert_single_type_to_sdl(type_def: Dict) -> str:
    """
    Convert single GraphQL type definition to SDL format
    
    Args:
        type_def: Type definition dictionary
        
    Returns:
        str: SDL format string
    """
    type_name = type_def.get("name", "")
    type_kind = type_def.get("kind", "")
    type_desc = type_def.get("description")
    
    if not type_name:
        return ""
    
    sdl_part = ""
    
    # Add description if available
    if type_desc:
        sdl_part += f'"""{type_desc}"""\n'
    
    if type_kind == "OBJECT":
        sdl_part += f"type {type_name} {{\n"
        fields = type_def.get("fields", [])
        for field in fields:
            field_name = field.get("name", "")
            field_type = format_type(field.get("type", {}))
            field_desc = field.get("description")
            if field_desc:
                sdl_part += f'  """{field_desc}"""\n'
            sdl_part += f"  {field_name}: {field_type}\n"
        sdl_part += "}"
        
    elif type_kind == "INPUT_OBJECT":
        sdl_part += f"input {type_name} {{\n"
        input_fields = type_def.get("inputFields", [])
        for field in input_fields:
            field_name = field.get("name", "")
            field_type = format_type(field.get("type", {}))
            field_desc = field.get("description")
            if field_desc:
                sdl_part += f'  """{field_desc}"""\n'
            sdl_part += f"  {field_name}: {field_type}\n"
        sdl_part += "}"
        
    elif type_kind == "ENUM":
        sdl_part += f"enum {type_name} {{\n"
        enum_values = type_def.get("enumValues", [])
        for enum_val in enum_values:
            val_name = enum_val.get("name", "")
            val_desc = enum_val.get("description")
            if val_desc:
                sdl_part += f'  """{val_desc}"""\n'
            sdl_part += f"  {val_name}\n"
        sdl_part += "}"
        
    elif type_kind == "INTERFACE":
        sdl_part += f"interface {type_name} {{\n"
        fields = type_def.get("fields", [])
        for field in fields:
            field_name = field.get("name", "")
            field_type = format_type(field.get("type", {}))
            field_desc = field.get("description")
            if field_desc:
                sdl_part += f'  """{field_desc}"""\n'
            sdl_part += f"  {field_name}: {field_type}\n"
        sdl_part += "}"
        
    elif type_kind == "UNION":
        possible_types = type_def.get("possibleTypes", [])
        if possible_types:
            type_names = [t.get("name", "") for t in possible_types if t.get("name")]
            sdl_part += f"union {type_name} = {' | '.join(type_names)}"
            
    elif type_kind == "SCALAR":
        sdl_part += f"scalar {type_name}"
    
    return sdl_part


def extract_referenced_types_from_type(type_def: Dict) -> Set[str]:
    """
    Extract all referenced type names from type definition
    
    Args:
        type_def: Type definition dictionary
        
    Returns:
        Set[str]: Set of referenced type names
    """
    referenced = set()
    
    def extract_from_type_ref(type_ref):
        if not type_ref:
            return
        
        if isinstance(type_ref, dict):
            if "name" in type_ref and type_ref["name"]:
                referenced.add(type_ref["name"])
            
            if "ofType" in type_ref:
                extract_from_type_ref(type_ref["ofType"])
    
    # Extract from fields
    if "fields" in type_def and type_def["fields"]:
        for field in type_def["fields"]:
            if "type" in field:
                extract_from_type_ref(field["type"])
            
            # Extract from field arguments
            if "args" in field and field["args"]:
                for arg in field["args"]:
                    if "type" in arg:
                        extract_from_type_ref(arg["type"])
    
    # Extract from input fields
    if "inputFields" in type_def and type_def["inputFields"]:
        for field in type_def["inputFields"]:
            if "type" in field:
                extract_from_type_ref(field["type"])
    
    # Extract from possible types (unions)
    if "possibleTypes" in type_def and type_def["possibleTypes"]:
        for possible_type in type_def["possibleTypes"]:
            if "name" in possible_type:
                referenced.add(possible_type["name"])
    
    # Extract from interfaces
    if "interfaces" in type_def and type_def["interfaces"]:
        for interface in type_def["interfaces"]:
            if "name" in interface:
                referenced.add(interface["name"])
    
    return referenced


def process_graphql_schema(schema_data: Dict, filter: Optional[str] = None, depth: int = 1, include_arg_descriptions: bool = True) -> str:
    """
    Abstract GraphQL schema processing method
    
    Args:
        schema_data: GraphQL schema JSON object
        filter: Smart filter, can be query field name or type name
                - If Query field name, process in query mode
                - If Type name, process in type mode
                - If both match, prioritize query mode processing
                - If empty, return all queries (depth=0 only)
        depth: Expansion depth
            - 0: Only return query itself (return type itself in type mode)
            - 1: Return query/type + parameter type definitions (do not continue expanding)
            - >1: Continue expanding types to specified depth
        include_arg_descriptions: Whether to include argument descriptions in SDL
        
    Note: filter parameter is required when depth > 0
    
    Returns:
        str: Generated SDL format string
    """
    if not schema_data or "types" not in schema_data:
        return ""
    
    # Validate parameters
    if depth > 0 and not filter:
        return "# Error: filter parameter is required when depth > 0"
    
    types = schema_data.get("types", [])
    
    # Build type lookup table
    type_lookup = {t.get("name"): t for t in types if t.get("name")}
    
    # Built-in type filtering
    builtin_scalars = {"String", "Int", "Float", "Boolean", "ID"}
    builtin_types = {"__Schema", "__Type", "__Field", "__InputValue", "__EnumValue", "__Directive", "__DirectiveLocation", "__TypeKind"}
    
    # Find Query type
    query_type = None
    for type_def in types:
        if type_def.get("name") == "Query" and type_def.get("kind") == "OBJECT":
            query_type = type_def
            break
    
    if not query_type or not query_type.get("fields"):
        return "# No Query type found in schema"
    
    # Intelligently detect whether filter is query field or type name
    if filter:
        # Check if it's a Query field (higher priority)
        query_fields = [f for f in query_type.get("fields", []) if f.get("name") == filter]
        is_query_field = len(query_fields) > 0
        
        # Check if it's a Type name
        is_type_name = filter in type_lookup
        
        if is_query_field:
            # Process in query mode
            return _process_query_filter_mode(filter, query_type, type_lookup, depth, builtin_scalars, builtin_types, include_arg_descriptions)
        elif is_type_name:
            # Process in type mode
            return _process_type_filter_mode(filter, type_lookup, depth, builtin_scalars, builtin_types)
        else:
            return f"# Error: '{filter}' not found as query field or type name"
    
    # No filter, return all queries (depth=0 only)
    if depth > 0:
        return "# Error: filter parameter is required when depth > 0"
    
    query_fields = query_type.get("fields", [])
    
    # Generate SDL parts
    sdl_parts = []
    
    # Generate Query type definition (all fields)
    query_fields_sdl = []
    for field in query_fields:
        field_name = field.get("name", "")
        field_type = format_type(field.get("type", {}))
        field_desc = field.get("description")
        
        if field_desc:
            query_fields_sdl.append(f'  """{field_desc}"""')
        
        # Process field arguments
        args = field.get("args", [])
        if args:
            args_str = []
            for arg in args:
                arg_name = arg.get("name", "")
                arg_type = format_type(arg.get("type", {}))
                arg_desc = arg.get("description")
                
                # Decide whether to include descriptions based on include_arg_descriptions parameter
                if include_arg_descriptions and arg_desc:
                    args_str.append(f'{arg_name}: {arg_type} # {arg_desc}')
                else:
                    args_str.append(f'{arg_name}: {arg_type}')
            query_fields_sdl.append(f'  {field_name}({", ".join(args_str)}): {field_type}')
        else:
            query_fields_sdl.append(f'  {field_name}: {field_type}')
    
    # Build Query SDL
    query_sdl = f'""" Query type (all fields) """\ntype Query {{\n' + '\n'.join(query_fields_sdl) + '\n}\n'
    sdl_parts.append(query_sdl)
    
    return '\n\n'.join(sdl_parts)
