from functions.function_implementations import (
    get_weather,
    default_function,
)


def process_function_calls(
    function_name, arguments
):

    if function_name == "get_weather":
        result = get_weather(
            arguments.get("location"),
            arguments.get("unit")
        )
    else:
        result = default_function()

    return result
