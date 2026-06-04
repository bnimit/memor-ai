from memor.tokencount import count_tokens

def test_count_tokens_english():
    # "Hello, world!" -> tiktoken cl100k_base gives 4 tokens; len//4 == 3
    text = "Hello, world!"
    result = count_tokens(text)
    assert isinstance(result, int)
    assert result > 0
    assert result != len(text) // 4

def test_count_tokens_code():
    text = 'def authenticate(user: str, password: str) -> bool:\n    return bcrypt.checkpw(password, user.hash)'
    result = count_tokens(text)
    assert isinstance(result, int)
    assert result > 0

def test_count_tokens_empty():
    assert count_tokens("") == 0

def test_count_tokens_short():
    assert count_tokens("hi") >= 1
