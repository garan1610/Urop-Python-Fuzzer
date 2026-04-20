import inspect
import random
import sys
import os
import json
import importlib
import multiprocessing
import ast
import textwrap
from collections import defaultdict

# --- 1. Top-Level Definitions (Picklable) ---

class FuzzGenerator:
    def __init__(self):
        # Basic types only to avoid external dependencies
        self.universe = [int, float, str, bool, type(None), list, tuple, dict]

    def get_random_type(self): 
        return random.choice(self.universe)

    def generate_value(self, t):
        try:
            if t is int: return random.randint(-10, 10)
            if t is float: return random.uniform(-10, 10)
            if t is str: return "fuzz"
            if t is bool: return True
            if t is list: return [1, 2]
            if t is tuple: return (1, 2)
            if t is dict: return {"k": 1}
            return None
        except: return 0

def make_concrete(abstract_cls):
    if not inspect.isabstract(abstract_cls): return abstract_cls
    def dummy_method(self, *args, **kwargs): return [1] 
    def dummy_property(self): return [0, 1]
    impl_methods = {}
    for name in abstract_cls.__abstractmethods__:
        base_attr = getattr(abstract_cls, name, None)
        if isinstance(base_attr, property): impl_methods[name] = property(dummy_property)
        else: impl_methods[name] = dummy_method
    return type(f"Concrete_{abstract_cls.__name__}", (abstract_cls,), impl_methods)

# --- 2. AST Helper (For Line Numbers & Calls ONLY) ---

class CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.calls = []

    def visit_Call(self, node):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            curr = node.func
            while isinstance(curr, ast.Attribute):
                parts.append(curr.attr)
                curr = curr.value
            if isinstance(curr, ast.Name):
                parts.append(curr.id)
            func_name = ".".join(reversed(parts))
        
        if func_name:
            self.calls.append(func_name)
        self.generic_visit(node)

def analyze_live_function(func_obj):
    """
    Takes a live function object (successful import), finds its source code, 
    and extracts metadata (line number, calls) using AST.
    """
    try:
        lines, start_lineno = inspect.getsourcelines(func_obj)
        source_code = "".join(lines)
        tree = ast.parse(textwrap.dedent(source_code))
        
        visitor = CallVisitor()
        visitor.visit(tree)
        return start_lineno, visitor.calls
    except Exception:
        return -1, []

# --- 3. The Worker Task ---

def worker_fuzz_task(sys_path_root, module_name, class_name, func_name, iterations, result_queue):
    """
    Worker now receives 'sys_path_root' explicitly to ensure it can import correctly.
    """
    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)
    
    # Silence output
    sys.stdout = open(os.devnull, 'w')
    sys.stderr = open(os.devnull, 'w')

    local_success_log = []

    try:
        mod = importlib.import_module(module_name)
        target_func = None
        instance = None
        
        if class_name:
            cls = getattr(mod, class_name)
            ConcreteCls = make_concrete(cls)
            try:
                # Try simple instantiation
                instance = ConcreteCls()
            except:
                try: 
                    # Try passing a list (common for vector-like classes)
                    instance = ConcreteCls([1,2])
                except: return 
            
            target_func = getattr(instance, func_name)
        else:
            target_func = getattr(mod, func_name)

        gen = FuzzGenerator()
        sig = inspect.signature(target_func)
        params = list(sig.parameters.values())
        
        for _ in range(iterations):
            args = []
            current_types = {}
            
            for p in params:
                if p.name in ['self', 'cls']: continue
                t = gen.get_random_type()
                if p.kind == p.VAR_POSITIONAL or p.kind == p.VAR_KEYWORD: continue

                val = gen.generate_value(t)
                args.append(val)
                current_types[p.name] = t.__name__
            
            try:
                target_func(*args)
                local_success_log.append(current_types)
            except Exception:
                # We expect crashes/exceptions during fuzzing, ignore them here
                pass

        result_queue.put(local_success_log)

    except Exception:
        # If the worker cannot import or find the function, it dies silently
        pass

# --- 4. The Safe Runner ---

def run_safely(sys_path_root, module_name, class_name, func_name, iterations=10):
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(
        target=worker_fuzz_task, 
        args=(sys_path_root, module_name, class_name, func_name, iterations, queue)
    )
    p.start()
    p.join(timeout=0.5) 
    
    if p.is_alive():
        p.terminate()
        p.join()
        return "TIMEOUT", []
    
    if p.exitcode != 0:
        return "CRASH", [] 
    
    try:
        if not queue.empty():
            return "SUCCESS", queue.get()
        else:
            return "SUCCESS", [] 
    except:
        return "ERROR", []

def update_stats(final_results, module_name, class_name, func_name, static_info, success_list):
    # Construct a unique key for the flat dictionary
    if class_name:
        full_key = f"{module_name}.{class_name}.{func_name}"
        is_class_method = True
    else:
        full_key = f"{module_name}.{func_name}"
        is_class_method = False

    # Initialize the entry if it doesn't exist
    if full_key not in final_results:
        final_results[full_key] = {
            "is_class_method": is_class_method,
            "params": {},
            "lineno": static_info['lineno'],
            "calls": static_info['calls']
        }

    target_block = final_results[full_key]

    if success_list:
        param_stats = target_block["params"]
        for run in success_list:
            for param, type_name in run.items():
                if param not in param_stats:
                    param_stats[param] = defaultdict(int)
                param_stats[param][type_name] += 1

# --- 5. Main Logic ---

def get_function_list(repo_root):
    iterations=20
    repo_root = os.path.abspath(repo_root)
    
    is_package = os.path.exists(os.path.join(repo_root, "__init__.py"))
    
    if is_package:
        sys_path_root = os.path.dirname(repo_root)
        package_prefix = os.path.basename(repo_root)
    else:
        sys_path_root = repo_root
        package_prefix = ""

    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)

    print(f"[*] Repository: {repo_root}")
    print(f"[*] Import Path: {sys_path_root}")
    if package_prefix:
        print(f"[*] Package Prefix: {package_prefix} (Treating as module)")

    # Changed from nested dicts to a single flat dictionary
    final_results = {}

    IGNORE_DIRS = {'tests', 'testing', 'benchmarks', 'examples', '_examples', 'conftest', 
                   'cython', 'include', 'distutils', 'f2py', '.git', '__pycache__', 'venv', 'env'}

    modules_processed = 0
    crashes_detected = 0

    class DefaultEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, defaultdict): return dict(obj)
            return super().default(obj)

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        for file in files:
            if file.endswith(".py") and not file.startswith("test") and not file.startswith("setup"):
                
                # Calculate the module string based on the sys_path_root
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, sys_path_root) # Path relative to the import root
                module_string = rel_path.replace(os.path.sep, ".")[:-3] # Remove .py
                
                if any(x in module_string.split(".") for x in IGNORE_DIRS): continue

                print(f"\r[>] Scanning: {module_string:<60}", end="")
                
                # Try to import
                try:
                    # Silence stdout during import (some libs print on init)
                    save_stdout = sys.stdout
                    sys.stdout = open(os.devnull, 'w')
                    mod = importlib.import_module(module_string)
                    sys.stdout = save_stdout
                except Exception as e:
                    sys.stdout = save_stdout
                    # Optional: print(f" [!] Failed: {e}", end="")
                    continue

                targets = []
                
                # Get Classes
                try:
                    classes = [m for name, m in inspect.getmembers(mod, inspect.isclass) if m.__module__ == mod.__name__]
                    for cls in classes:
                        methods = inspect.getmembers(cls, predicate=lambda x: inspect.isfunction(x) or inspect.ismethod(x))
                        for name, func_obj in methods:
                            if not name.startswith("__") or name == '__call__':
                                targets.append((cls.__name__, name, func_obj))
                except: pass
                
                # Get Functions
                try:
                    funcs = [f for name, f in inspect.getmembers(mod, inspect.isfunction) if f.__module__ == mod.__name__]
                    for f in funcs:
                        targets.append((None, f.__name__, f))
                except: pass

                # Fuzz Targets
                for cls_name, func_name, func_obj in targets:
                    
                    # 1. Metadata Extraction (using AST on the live object source)
                    lineno, calls = analyze_live_function(func_obj)
                    static_info = {"lineno": lineno, "calls": calls}
                    
                    # 2. Fuzzing
                    status, results = run_safely(sys_path_root, module_string, cls_name, func_name, iterations)
                    
                    if status == "SUCCESS":
                        update_stats(final_results, module_string, cls_name, func_name, static_info, results)
                    elif status == "CRASH":
                        crashes_detected += 1
                        # Save the function entry even if it crashed, so we know it exists
                        update_stats(final_results, module_string, cls_name, func_name, static_info, [])
                
                modules_processed += 1
                if modules_processed % 5 == 0:
                    with open("fuzz_results.json", 'w') as f:
                        json.dump(final_results, f, indent=4, cls=DefaultEncoder)

    print(f"\n\n[*] Fuzzing complete.")
    print(f"[*] Total Crashes survived: {crashes_detected}")
    
    with open("fuzz_results.json", 'w') as f:
         json.dump(final_results, f, indent=4, cls=DefaultEncoder)

    return final_results

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fuzzer9.py <repo_root>")
    else:
        results_dict = get_function_list(sys.argv[1])