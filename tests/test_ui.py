import pytest
from application.service import Service
from presentation.main import MainWindow
import pandas as pd


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(Service(tmp_path / "data"), first_run=False)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.job is None, timeout=10000)
    yield w
    w.close()
    qtbot.waitUntil(lambda: not w.jobs, timeout=10000)


@pytest.mark.parametrize("page", range(12))
def test_all_pages_smoke(window, qtbot, page):
    window.go(page)
    qtbot.wait(20)
    assert window.stack.currentIndex() == page
    assert window.heading.text()
    assert window.stack.currentWidget().isVisible()


def test_model_sort_search_copy(window):
    frame = pd.DataFrame({"code": ["600001", "600002"], "score": [1.0, 2.0]})
    window.results.set_frame(frame)
    window.results.proxy.setFilterFixedString("600002")
    assert window.results.proxy.rowCount() == 1
    window.results.set_frame(frame.assign(score=[3.0, 4.0]))
    assert window.results.source.data(window.results.source.index(0, 1)) == "3.0000"
