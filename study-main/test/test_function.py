from function import get_formatted_name

def test_get_formatted_name():
    name = get_formatted_name('zhang', 'san')
    assert name == 'Zhang San'

def test_get_formatted_middle_name():
    name = get_formatted_name('zhang', 'san', 'lao')
    assert name == 'Zhang Lao San'

