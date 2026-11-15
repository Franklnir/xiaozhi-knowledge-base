"""Calculator service for math operations."""
import ast
import math
import operator
import re
from typing import Any, Dict


_BINARY_OPERATORS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Pow: operator.pow,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_FUNCTIONS = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "pow": operator.pow, "int": int, "float": float, "sqrt": math.sqrt,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
    "log10": math.log10, "ceil": math.ceil, "floor": math.floor,
}
_MATH_NAMES = set(_FUNCTIONS) - {"abs", "round", "min", "max", "sum", "int", "float"}
_MATH_NAMES.update(_CONSTANTS)
_WORDS = {
    "kali": "*", "x": "*", "bagi": "/", "tambah": "+", "kurang": "-",
    "pangkat": "**", "akar": "sqrt", "ln": "log",
}


def _bounded_number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError
    return value


def _power(base, exponent):
    if abs(exponent) > 100:
        raise ValueError
    return _bounded_number(operator.pow(base, exponent))


def _evaluate(node):
    if isinstance(node, ast.Constant):
        return _bounded_number(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
        value = _evaluate(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            return _power(left, right)
        return _bounded_number(_BINARY_OPERATORS[type(node.op)](left, right))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        name = node.func.id
        if name not in _FUNCTIONS:
            raise ValueError
        if name in {"sum", "min", "max"} and len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
            args = [[_evaluate(item) for item in node.args[0].elts]]
        else:
            args = [_evaluate(arg) for arg in node.args]
        if name == "pow":
            return _power(*args)
        if name == "round" and len(args) == 2 and abs(args[1]) > 100:
            raise ValueError
        return _bounded_number(_FUNCTIONS[name](*args))
    raise ValueError


def safe_eval(expression: str) -> Dict[str, Any]:
    """Evaluate bounded arithmetic and allowlisted numeric functions."""
    expression = expression.strip()
    if not expression:
        return {"success": False, "error": "Ekspresi kosong"}
    if len(expression) > 512:
        return {"success": False, "error": "Ekspresi terlalu panjang"}
    if "#" in expression:
        return {"success": False, "error": "Ekspresi matematika tidak valid."}

    expression = re.sub(r"^(?:berapa(?: hasil(?: dari)?)?|hitung(?:kan)?)\s+", "", expression.lower()).rstrip(" ?")
    conversion = re.fullmatch(r"konversi\s+([+-]?\d+(?:[.,]\d+)?)\s+([a-z]+)\s+ke\s+([a-z]+)", expression)
    if conversion:
        units = {"fahrenheit": "f", "celsius": "c", "celcius": "c", "kelvin": "k"}
        value, source, target = conversion.groups()
        value = float(value.replace(",", "."))
        if not math.isfinite(value) or abs(value) > 1e100:
            return {"success": False, "error": "Nilai konversi melebihi batas."}
        return convert_unit(value, units.get(source, source), units.get(target, target))
    expression = re.sub(r"akar\s+(?:dari\s+)?([+-]?\d+(?:[.,]\d+)?)", r"sqrt(\1)", expression)
    expression = re.sub(r"(\d+(?:\.\d+)?)\s*%(\s*dari\s+)?",
                        lambda match: f"({match[1]}/100)" + (" * " if match[2] else ""), expression)
    expression = expression.translate(str.maketrans({"×": "*", "÷": "/", "−": "-", "^": "**"}))
    expression = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expression)
    expression = re.sub(r"\b(?:" + "|".join(_WORDS) + r")\b", lambda match: _WORDS[match[0]], expression)
    expression = re.sub(r"\bmath\.([a-z][a-z0-9]*)\b", lambda match: match[1] if match[1] in _MATH_NAMES else match[0], expression)
    try:
        parsed = ast.parse(expression.strip(), mode="eval")
        if sum(1 for _ in ast.walk(parsed)) > 100:
            raise ValueError
        result = _evaluate(parsed.body)
    except ZeroDivisionError:
        return {"success": False, "error": "Tidak bisa dibagi dengan nol"}
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError):
        return {"success": False, "error": "Ekspresi matematika tidak valid atau melebihi batas."}

    if isinstance(result, float):
        result = int(result) if result.is_integer() else round(result, 10)
    return {"success": True, "expression": expression, "result": result, "message": f"Hasil: {result}"}


def convert_unit(value: float, from_unit: str, to_unit: str) -> Dict[str, Any]:
    """Convert between units."""
    conversions = {
        # Length
        ('m', 'km'): lambda x: x / 1000,
        ('km', 'm'): lambda x: x * 1000,
        ('m', 'cm'): lambda x: x * 100,
        ('cm', 'm'): lambda x: x / 100,
        ('m', 'ft'): lambda x: x * 3.28084,
        ('ft', 'm'): lambda x: x / 3.28084,
        ('km', 'mile'): lambda x: x * 0.621371,
        ('mile', 'km'): lambda x: x / 0.621371,
        # Temperature
        ('c', 'f'): lambda x: x * 9/5 + 32,
        ('f', 'c'): lambda x: (x - 32) * 5/9,
        ('c', 'k'): lambda x: x + 273.15,
        ('k', 'c'): lambda x: x - 273.15,
        # Weight
        ('kg', 'lb'): lambda x: x * 2.20462,
        ('lb', 'kg'): lambda x: x / 2.20462,
        ('kg', 'g'): lambda x: x * 1000,
        ('g', 'kg'): lambda x: x / 1000,
    }

    key = (from_unit.lower(), to_unit.lower())
    if key not in conversions:
        return {"success": False, "error": f"Konversi {from_unit} → {to_unit} tidak didukung"}

    try:
        result = conversions[key](value)
        return {
            "success": True,
            "from": f"{value} {from_unit}",
            "to": f"{result} {to_unit}",
            "result": result,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
