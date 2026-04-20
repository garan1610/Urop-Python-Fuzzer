from typing import Dict, List, Any, Set

# def merge_list_dicts_stable(
#     *dicts: Dict[Any, List[Any]]
# ) -> Dict[Any, List[Any]]:
#     """
#     Deeply merges multiple dictionaries where values are lists.
    
#     - Lists are combined without sorting.
#     - Merging is stable: elements from the first encountered list for a key 
#       maintain their original order, followed by new unique elements from 
#       subsequent lists.
#     - Duplicates are removed.
    
#     Args:
#         *dicts: One or more dictionaries to merge.
        
#     Returns:
#         A single dictionary containing the merged and deduplicated lists.
#     """
#     if not dicts:
#         return {}

#     # Stores the final merged lists (the output structure)
#     final_result: Dict[Any, List[Any]] = {}
    
#     # Stores all items encountered so far for a key (used for fast deduplication check)
#     seen_items: Dict[Any, Set[Any]] = {}

#     # Iterate through all dictionaries provided in order (stability achieved here)
#     for current_dict in dicts:
#         for key, values in current_dict.items():
            
#             # Ensure the key is initialized in the final result and seen_items tracker
#             if key not in final_result:
#                 final_result[key] = []
#                 seen_items[key] = set()
            
#             # Iterate through the values of the current list
#             if isinstance(values, list):
#                 iterable_values = values
#             else:
#                 # Handle single non-list values (e.g., if a value was just '1')
#                 iterable_values = [values]
                
#             for value in iterable_values:
#                 # Check if the item has already been seen for this key
#                 if value not in seen_items[key]:
#                     # If it's new, append it to the final list
#                     final_result[key].append(value)
#                     # And mark it as seen
#                     seen_items[key].add(value)
            
#     return final_result
