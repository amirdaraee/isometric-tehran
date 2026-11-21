"""runpod.session: several blocks on one pod, in order, stopping on failure or at the cost cap; the pod always
ends. No network: the pod and the remote painting are replaced by fakes."""

import pytest

from paint import runpod


class FakePod:
    made: list["FakePod"] = []

    def __init__(self, name, max_price, gpus=None):
        self.name, self.price, self.spent, self.deleted = name, 2.0, 0.0, False
        FakePod.made.append(self)

    def create(self):
        pass

    def wait_ssh(self):
        pass

    def delete(self):
        self.deleted = True

    def cost(self):
        return self.spent


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakePod.made = []
    painted = []
    fail = set()

    def paint(pod, d, version, args, hours):
        pod.spent += 0.5
        painted.append(d.name)
        if d.name in fail:
            return 1
        (d / f"canvas_{version}.png").write_bytes(b"x")
        return 0

    monkeypatch.setattr(runpod, "Pod", FakePod)
    monkeypatch.setattr(runpod, "_setup", lambda pod: None)
    monkeypatch.setattr(runpod, "_paint", paint)

    def prepare(bid):
        d = tmp_path / bid
        d.mkdir(exist_ok=True)
        return d

    return painted, fail, prepare


def run(prepare, bids, max_cost=None, finished=None):
    return runpod.session(bids, "day", [], lambda bid, first: 0.5, 3.0, prepare,
                          lambda bid, d: None if finished is None else finished.append(bid), max_cost=max_cost)


def test_paints_in_order_on_one_pod(fake):
    painted, _, prepare = fake
    finished = []
    assert run(prepare, ["b00", "b01", "b02"], finished=finished) == ["b00", "b01", "b02"]
    assert painted == finished == ["b00", "b01", "b02"]
    assert len(FakePod.made) == 1 and FakePod.made[0].deleted


def test_stops_at_a_failed_block(fake):
    painted, fail, prepare = fake
    fail.add("b01")
    assert run(prepare, ["b00", "b01", "b02"]) == ["b00"]
    assert painted == ["b00", "b01"]
    assert FakePod.made[0].deleted


def test_no_block_starts_past_the_cost_cap(fake):
    painted, _, prepare = fake
    # a block may cost up to its cap, 0.5 h x $2/h = $1, and really costs $0.5: the third would start at $1 spent,
    # and $1 + $1 is over $1.9
    assert run(prepare, ["b00", "b01", "b02"], max_cost=1.9) == ["b00", "b01"]
    assert FakePod.made[0].deleted


def test_pod_deleted_on_errors(fake, monkeypatch):
    _, _, prepare = fake

    def boom(pod, d, version, args, hours):
        raise RuntimeError("ssh broke")

    monkeypatch.setattr(runpod, "_paint", boom)
    with pytest.raises(RuntimeError):
        run(prepare, ["b00"])
    assert FakePod.made[0].deleted
