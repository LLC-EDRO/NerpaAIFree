import unittest
from types import SimpleNamespace
from runtime.native_budget import _capacity

class CounterFitter:
    def __init__(self, capacity):self.capacity=capacity;self.glyphs=0
    def fit(self,frame,lines):
        self.glyphs+=len(lines[0])
        return SimpleNamespace(status='fits' if len(lines[0])<=self.capacity else 'overflow')

class BudgetSearchTest(unittest.TestCase):
    def test_exact_capacity_without_large_probes_for_small_frames(self):
        for cap in (1,2,15,16,17,31,32,33,120,300,1799,1800):
            fitter=CounterFitter(cap)
            value,report=_capacity(None,'sample phrase '*5,fitter)
            self.assertEqual(value,cap)
            self.assertEqual(report.status,'fits')
            if cap<=33:self.assertLess(fitter.glyphs,400)
