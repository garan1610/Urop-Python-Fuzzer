class CustomClass:
    def __init__(self, x: int):
        self.x = x

def main(x):
    obj = CustomClass(int(x))
    
def add_object(x: int, obj: CustomClass) -> int:
    return obj.x + x
