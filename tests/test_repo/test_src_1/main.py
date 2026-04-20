def main():
	print("This is src_1 main.py")

def param_func(a):
	return a + 1

def param_func_2(x: int) -> int:
	return x * 2

def nested_func():
	def inner_func():
		return "Inner Function Result"
	return inner_func()

def combined_func():
	return param_func(3) + param_func_2(5)

def str_func(s: str) -> str:
	return s.upper()

from test_src_2.main import SampleClass

def use_sample_class():
	obj = SampleClass()
	result_one = obj.method_one(10)
	result_two = obj.method_two(20)
	return result_one, result_two

def error_func(a: int):
	return 1 / (a - 1) 
