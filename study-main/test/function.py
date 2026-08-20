

def get_formatted_name(first, last, middle=''):
    if not middle:
        full_name = f'{first} {last}'
    else:
        full_name = f'{first} {middle} {last}'
    return full_name.title()


