from memor.supersession import validity_for

def test_validity_curve():
    assert validity_for(0) == 1.0
    assert validity_for(1) == 0.5
    assert validity_for(2) == 0.25
    assert validity_for(3) == 0.25   # floored
