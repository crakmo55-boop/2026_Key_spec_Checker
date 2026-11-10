# -*- coding: utf-8 -*-
"""Key spec 대체 규칙 (2026-11-10 사용자 지시) - Popup / Landing 공통.

  "요청한 제품에 Key spec 이 없으면 mapping table 에서 다른 제품을 선정해서, 그 사업부에 한하여 다시 찾아라"

판정 규칙(기존 Key spec 코드)은 바꾸지 않는다. 바꾸는 것은 '어떤 제품으로 검증할지' 뿐이다.
후보 = 같은 사업부 · 같은 Set 카테고리의 매핑 대체 후보(ProductSet.alternates, 규칙 순서) 중
       같은 사업부 다른 Set 에서 이미 쓴 제품과 현재 Set 제품을 뺀 것.

제품별 상태(verdict)
  PASS    : 이 열 Key Specs 값 있음
  EMPTY   : 섹션은 보이는데 이 열 값이 없음            → 원인 제품이 확실 → 그 제품만 바꾼다
  NOSKU   : Compare 화면에 이 SKU 열이 없음             → 그 제품만 바꾼다
  HIDDEN  : Key Specs 섹션 전체 미노출 (세트 단위 규칙) → 원인 제품 미확정
  NOPOPUP : 새 Compare 팝업이 뜨지 않음 (세트 단위)     → 원인 제품 미확정
  BUTTON  : Compare 버튼에 3개 SKU 가 반영되지 않음      → 원인 제품 미확정
원인 미확정이면 한 번에 한 제품씩 바꿔 본다(나머지 2개는 그대로). 바꿨더니 해결되면 그 제품이 원인으로 '확인' 되고,
해결되지 않으면 원래 제품으로 되돌리고 다음 제품을 바꿔 본다. 세 제품을 하나씩 다 바꿔도 안 되면(원인 2개 이상 등)
남은 후보로 Set 전체를 바꾼다. 시도 횟수는 KEYSPEC_SUBSTITUTE_ROUNDS, 시간은 Set 예산으로 제한한다.
"""
import logging
import time

V_PASS, V_EMPTY, V_HIDDEN = "PASS", "EMPTY", "HIDDEN"
V_NOSKU, V_NOPOPUP, V_BUTTON = "NOSKU", "NOPOPUP", "BUTTON"
SET_LEVEL = (V_HIDDEN, V_NOPOPUP, V_BUTTON)

REASON = {
    V_EMPTY: "Key Specs 값 없음 (해당 열 값이 비어 있음)",
    V_NOSKU: "Compare 화면에 제품 열이 표시되지 않음 (Key Spec 확인 불가)",
    V_HIDDEN: "Key Specs 없음 (세트 Key Specs 섹션 미노출의 원인 제품으로 확인)",
    V_NOPOPUP: "새 Compare 팝업 미표시의 원인 제품으로 확인 (Key Spec 확인 불가)",
    V_BUTTON: "Compare 버튼에 반영되지 않는 제품으로 확인 (팝업 열기 불가)",
}


class KeySpecRetry:
    """Set 1개의 대체 진행 상태. checker 가 evaluate → decide → (realize) 를 반복한다."""

    def __init__(self, products, pool, max_rounds, budget_s, label=""):
        self.products = list(products)
        self.initial = list(products)          # 대체 전 제품 (PF/드롭다운 대체 반영 후)
        self.pool = list(pool)                 # 남은 후보 (규칙 순서)
        self.max_rounds = max_rounds
        self.deadline = time.monotonic() + budget_s
        self.label = label
        self.rounds = 0
        self.replaced = {}                     # 새 SKU → (원래 Product, 사유)
        self.probe = None                      # (idx, 원래 Product) - 원인 찾기 중 바꿔 둔 자리
        self.probe_idx = 0
        self._probe_kind = None
        self.full_swaps = 0
        self.log = []                          # 비고용 시도 기록
        self.stop_reason = ""

    # ---------------------------------------------------------------- 후보
    def _codes(self):
        return {p.rep_model for p in self.products}

    def take(self, avoid=()):
        """다음 후보 (현재 Set·avoid 에 없는 것). 없으면 None."""
        while self.pool:
            cand = self.pool.pop(0)
            if cand.rep_model not in self._codes() and cand.rep_model not in avoid:
                return cand
        return None

    def unavailable(self, idx, cand, why):
        """realize 단계에서 후보를 화면에 올리지 못함 → 같은 자리에 다음 후보. 반환: 새 후보 또는 None."""
        self.log.append(f"{cand.rep_model} {why}")
        nxt = self.take()
        if nxt is None:
            return None
        orig_item = self.replaced.pop(cand.rep_model, None)
        self.products[idx] = nxt
        if orig_item:                              # 대체로 넣으려던 후보 → 원래 사유를 다음 후보로 넘김
            self.replaced[nxt.rep_model] = orig_item
        elif not (self.probe and self.probe[0] == idx):
            self.replaced[nxt.rep_model] = (cand, why)   # 원래 제품 자체를 화면에 올리지 못함
        # 원인 찾기 중인 자리면 probe 는 그대로 (같은 자리를 다른 후보로 시험)
        return nxt

    # ---------------------------------------------------------------- 결정
    def out_of_budget(self) -> bool:
        if self.rounds >= self.max_rounds:
            self.stop_reason = f"대체 시도 {self.rounds}회 한도"
            return True
        if time.monotonic() > self.deadline:
            self.stop_reason = "Set 처리 시간 한도"
            return True
        return False

    def decide(self, verdicts) -> bool:
        """verdicts[i] = self.products[i] 의 상태. 제품을 바꿔 다시 확인해야 하면 True (self.products 갱신)."""
        set_level = any(v in SET_LEVEL for v in verdicts)

        # 원인 찾기 중이던 자리: 세트 단위 문제가 사라졌으면 원래 제품이 원인으로 확인
        if self.probe and not set_level:
            idx, orig = self.probe
            new = self.products[idx]
            why = REASON[self._probe_kind]
            self.replaced[new.rep_model] = (orig, why)
            logging.warning("[KEYSPEC-대체] %s %s %s → %s (원인 확인)", self.label, orig.rep_model, why, new.rep_model)
            self.log.append(f"{orig.rep_model}→{new.rep_model} 해결(원인 확인)")
            self.probe = None

        if all(v == V_PASS for v in verdicts):
            return False
        if self.max_rounds <= 0 or self.out_of_budget():
            return False

        changed = False
        if not set_level:
            # 원인이 확실한 열만 교체
            for i, v in enumerate(verdicts):
                if v in (V_EMPTY, V_NOSKU):
                    cand = self.take()
                    if cand is None:
                        self.stop_reason = "남은 대체 후보 없음"
                        break
                    orig = self.products[i]                      # 비고에는 실제로 Key Specs 가 없던 제품을 남긴다
                    self.replaced.pop(orig.rep_model, None)
                    self.products[i] = cand
                    self.replaced[cand.rep_model] = (orig, REASON[v])
                    logging.warning("[KEYSPEC-대체] %s %s %s → %s", self.label, orig.rep_model, REASON[v], cand.rep_model)
                    self.log.append(f"{orig.rep_model}({v})→{cand.rep_model}")
                    changed = True
        else:
            kind = next(v for v in verdicts if v in SET_LEVEL)
            if self.probe:                         # 바꿔 봤는데도 그대로 → 되돌리고 다음 자리
                idx, orig = self.probe
                self.log.append(f"{self.products[idx].rep_model} 로 {idx + 1}번 교체해도 {kind} 유지 → 원래 제품 복원")
                self.products[idx] = orig
                self.probe = None
                self.probe_idx = idx + 1
            if self.probe_idx < len(self.products):
                cand = self.take()
                if cand is not None:
                    idx = self.probe_idx
                    self.probe = (idx, self.products[idx])
                    self._probe_kind = kind
                    self.log.append(f"{kind}: {idx + 1}번 {self.products[idx].rep_model} → {cand.rep_model} 시험")
                    self.products[idx] = cand
                    changed = True
                else:
                    self.stop_reason = "남은 대체 후보 없음"
            elif self.full_swaps < 2:              # 하나씩 바꿔도 안 됨 → Set 전체 교체
                cands = []
                while len(cands) < len(self.products):
                    c = self.take(avoid={x.rep_model for x in cands})
                    if c is None:
                        break
                    cands.append(c)
                if len(cands) == len(self.products):
                    self.full_swaps += 1
                    why = (f"세트 단위 {kind} - 한 제품씩 바꿔도 해결되지 않아 원인 제품 미확정, Set 전체 교체")
                    for i, c in enumerate(cands):
                        orig = self.products[i]
                        self.replaced.pop(orig.rep_model, None)
                        self.replaced[c.rep_model] = (orig, why)
                    self.log.append(f"{kind}: Set 전체 교체 → {[c.rep_model for c in cands]}")
                    logging.warning("[KEYSPEC-대체] %s %s → Set 전체 교체 %s", self.label,
                                    [p.rep_model for p in self.products], [c.rep_model for c in cands])
                    self.products = cands
                    changed = True
                else:
                    self.stop_reason = "Set 전체를 바꿀 후보 부족"
            else:
                self.stop_reason = "Set 전체 교체 2회 후에도 해결 안 됨"
        if changed:
            self.rounds += 1
        return changed

    def finalize(self, evaluated=None):
        """마지막 확인 결과로 끝낼 때. evaluated = 마지막으로 실제 확인한 제품 목록 (없으면 self.products).
        원인 찾기 중 바꿔 둔 제품이 결과에 남아 있으면 비고에 그 사실과 원래 제품을 남긴다."""
        evaluated = list(evaluated or self.products)
        why = f"Key Spec 세트 문제의 원인 확인 중 교체한 상태로 종료 ({self.stop_reason or '시도 한도'}) - 문제 계속"
        initial = {p.rep_model for p in self.initial}
        for i, p in enumerate(evaluated):
            if p.rep_model in self.replaced or p.rep_model in initial:
                continue
            # 원래 Set 에 없던 제품인데 대체 기록이 없음 = 원인 찾기 중 시험으로 넣은 제품
            orig = self.probe[1] if self.probe and self.probe[0] == i else self.initial[i]
            self.replaced[p.rep_model] = (orig, why)
        self.products = evaluated
        self.probe = None

    def summary(self) -> str:
        """비고에 붙일 시도 요약 (대체가 없었으면 '')."""
        if not self.log and not self.stop_reason:
            return ""
        parts = []
        if self.log:
            parts.append("대체 시도: " + "; ".join(self.log[-6:]))
        if self.stop_reason:
            parts.append(f"중단: {self.stop_reason}")
        return " | ".join(parts)
