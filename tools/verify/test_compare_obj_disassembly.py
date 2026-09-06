"""Whole-object boundaries must preserve out-of-line MSVC switch arms."""
import subprocess
import unittest
from unittest.mock import patch

from tools.verify import compare_obj


def parse(text):
    result = subprocess.CompletedProcess([], 0, stdout=text, stderr="")
    with patch.object(compare_obj.subprocess, "run", return_value=result):
        return compare_obj.disassemble("fixture.obj")


class DisassemblyBoundariesTests(unittest.TestCase):
    def test_switch_arm_after_last_return_and_before_table(self):
        functions = parse("""<_unit_animation_set_state>:
    jmp *0x0(,%edx,4)
<$Lreturn>:
    popl %ebp
    retl
<$Lweapon_idle>:
    movswl 0x12(%ecx), %ecx
    calll 0x0 <_model_animation_choose_random>
    jmp 0x4 <$Lreturn>
    addb %al, (%eax)
<_next>:
    retl
""")
        self.assertEqual(functions["unit_animation_set_state"], [
            "jmp *0x0(,%edx,4)", "popl %ebp", "retl",
            "movswl 0x12(%ecx), %ecx",
            "calll 0x0 <_model_animation_choose_random>", "jmp 0x4 <$Lreturn>",
        ])
        self.assertEqual(functions["next"], ["retl"])

    def test_symbol_offset_back_edge_preserves_tail(self):
        functions = parse("""<_FUN_00100000>:
    jmp *0x0(,%eax,4)
    retl
<$Ltail>:
    movl $0x1, %eax
    jmp 0x2 <_FUN_00100000+0x2>
""")
        self.assertEqual(len(functions["FUN_00100000"]), 4)

    def test_table_without_internal_back_edge_is_trimmed(self):
        functions = parse("""<_table_owner>:
    jmp *0x0(,%eax,4)
    retl
<$Ltable>:
    addb $0x4, %al
    addb %al, (%esp,%eax)
""")
        self.assertEqual(functions["table_owner"], ["jmp *0x0(,%eax,4)", "retl"])

    def test_next_function_labels_do_not_protect_prior_table(self):
        functions = parse("""<_first>:
    jmp *0x0(,%eax,4)
    retl
    jmp 0x0 <_second>
<_second>:
    retl
""")
        self.assertEqual(functions["first"], ["jmp *0x0(,%eax,4)", "retl"])
        self.assertEqual(functions["second"], ["retl"])


if __name__ == "__main__":
    unittest.main()
