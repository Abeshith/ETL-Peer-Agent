"""
Finding Formatter Utility
------------------------
Converts finding strings to markdown format with bold keys for better readability.
"""

import re


def bold_finding_keys(finding: str) -> str:
    """
    Convert pipe-delimited findings to markdown with bold keys.
    
    Input:  "db: LANDING | schema: LND | tables: raw_data"
    Output: "**db**: LANDING | **schema**: LND | **tables**: raw_data"
    
    Also handles:
    - Single findings: "imports: AYSnowflakeLoader" → "**imports**: AYSnowflakeLoader"
    - Mixed content with parentheses: "mechanism: Azure Key Vault (AyAzureKeyVault)" 
      → "**mechanism**: Azure Key Vault (AyAzureKeyVault)"
    """
    if not finding or not isinstance(finding, str):
        return finding
    
    # Split by pipe delimiter, process each part, rejoin
    parts = finding.split("|")
    formatted_parts = []
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # Match "key: value" pattern at the start of the part
        # key can contain letters, numbers, underscores, hyphens, underscores
        match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_\-]*)\s*:\s*(.*)$', part)
        
        if match:
            key, value = match.groups()
            # Bold the key
            formatted_parts.append(f"**{key}**: {value}")
        else:
            # No key:value pattern, keep as-is
            formatted_parts.append(part)
    
    return " | ".join(formatted_parts) if formatted_parts else finding


def format_all_findings(test_results: list) -> list:
    """
    Apply bold_finding_keys to all test results in a list.
    Modifies test results in-place.
    """
    for result in test_results:
        if hasattr(result, 'finding') and result.finding:
            result.finding = bold_finding_keys(result.finding)
    return test_results
