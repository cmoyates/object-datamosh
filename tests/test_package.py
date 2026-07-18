from pytest import CaptureFixture

from object_datamosh import main


def test_main_prints_project_greeting(capsys: CaptureFixture[str]) -> None:
    main()

    assert capsys.readouterr().out == "Hello from object-datamosh!\n"
