"""Calculator service for math operations."""
import math
import re
from typing import Any, Dict


def safe_eval(expression: str) -> Dict[str, Any]:
    """Safely evaluate a math expression."""
    # Clean the expression
    expression = expression.strip()
    if not expression:
        return {"success": False, "error": "Ekspresi kosong"}

    # Replace common math words
    replacements = {
        'kali': '*', 'x': '*', '×': '*', '÷': '/',
        'bagi': '/', 'tambah': '+', 'kurang': '-',
        'pangkat': '**', 'akar': 'math.sqrt',
        'sin': 'math.sin', 'cos': 'math.cos', 'tan': 'math.tan',
        'log': 'math.log', 'ln': 'math.log', 'pi': 'math.pi',
        'e': 'math.e', '%': '/100',
    }
    for word, op in replacements.items():
        expression = expression.replace(word, op)

    # Security: only allow safe characters
    safe_pattern = r'^[\d\s\+\-\*\/\.\(\)\%\,\math\.a-zA-Z]+$'
    if not re.match(safe_pattern, expression):
        return {"success": False, "error": "Ekspresi mengandung karakter tidak diizinkan"}

    # Block dangerous functions
    dangerous = ['import', 'exec', 'eval', 'open', 'file', 'system', '__']
    for d in dangerous:
        if d in expression.lower():
            return {"success": False, "error": "Ekspresi mengandung fungsi berbahaya"}

    try:
        # Create safe math context
        safe_dict = {
            "abs": abs, "round": round, "min": min, "max": max,
            "sum": sum, "pow": pow, "int": int, "float": float,
            "math": math,
            "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
            "tan": math.tan, "log": math.log, "log10": math.log10,
            "pi": math.pi, "e": math.e,
            "ceil": math.ceil, "floor": math.floor,
        }
        result = eval(expression, {"__builtins__": {}}, safe_dict)

        # Format result
        if isinstance(result, float):
            if result == int(result):
                result = int(result)
            else:
                result = round(result, 10)

        return {
            "success": True,
            "expression": expression,
            "result": result,
            "message": f"Hasil: {result}",
        }
    except ZeroDivisionError:
        return {"success": False, "error": "Tidak bisa dibagi dengan nol"}
    except Exception as e:
        return {"success": False, "error": f"Error: {str(e)[:100]}"}


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
