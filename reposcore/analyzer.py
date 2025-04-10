#!/usr/bin/env python3

from typing import Dict, Optional

import matplotlib.pyplot as plt
import pandas as pd
import requests
from prettytable import PrettyTable

from .utils.retry_request import retry_request

class RepoAnalyzer:
    """Class to analyze repository participation for scoring"""

    def __init__(self, repo_path: str, token: Optional[str] = None):
        self.repo_path = repo_path
        self.participants: Dict = {}
        self.score_weights = {
            'PRs': 1,  # 이 부분은 merge된 PR 및 정상 이슈 갯수만 세기 위해 임시로 1로 유지
            'issues_created': 1,
            'issue_comments': 1
        }

        self._data_collected = True  # 기본값을 True로 설정

        self.SESSION = requests.Session()
        self.SESSION.headers.update({'Authorization': token}) if token else None

    def collect_PRs_and_issues(self) -> None:
        """
        GitHub 저장소의 PR 및 이슈를 수집하여 점수에 반영

        - PR: 병합된 PR만 점수 부여 (merged_at != None)
        - Issue: open / reopened / completed 상태만 점수 부여
            * open: state_reason == None
            * reopened: state_reason == 'reopened'
            * completed: state_reason == 'completed'
            * not_planned 상태 이슈는 점수 제외
        """
        page = 1
        per_page = 100

        while True:
            url = f"https://api.github.com/repos/{self.repo_path}/issues"

            response = retry_request(self.SESSION,
                                     url,
                                     max_retries=3,
                                     params={
                                         'state': 'all',
                                         'per_page': per_page,
                                         'page': page
                                     })

            if response.status_code == 403:
                print("⚠️ 요청 실패 (403): GitHub API rate limit에 도달했습니다.")
                self._data_collected = False
                return
            elif response.status_code != 200:
                print(f"⚠️ GitHub API 요청 실패: {response.status_code}")
                self._data_collected = False
                return

            items = response.json()
            if not items:
                break

            for item in items:
                author = item.get('user', {}).get('login', 'Unknown')
                if author not in self.participants:
                    self.participants[author] = {
                        'p_enhancement': 0,
                        'p_bug': 0,
                        'p_documentation': 0,
                        'i_enhancement': 0,
                        'i_bug': 0,
                        'i_documentation': 0,
                    }

                labels = item.get('labels', [])
                label_names = [label.get('name', '').lower() for label in labels if label.get('name')]

                state_reason = item.get('state_reason')

                # PR 처리 (병합된 PR만)
                if 'pull_request' in item:
                    merged_at = item.get('pull_request', {}).get('merged_at')
                    if merged_at:
                        for label in label_names:
                            key = f'p_{label}'
                            if key in self.participants[author]:
                                self.participants[author][key] += 1

                # 이슈 처리 (open / reopened / completed 만 포함)
                else:
                    if state_reason in ('completed', 'reopened', None):
                        for label in label_names:
                            key = f'i_{label}'
                            if key in self.participants[author]:
                                self.participants[author][key] += 1

            # 다음 페이지 검사
            link_header = response.headers.get('link', '')
            if 'rel="next"' in link_header:
                page += 1
            else:
                break

        if not self.participants:
            print("⚠️ 수집된 데이터가 없습니다. (참여자 없음)")
        else:
            print("\n✅ 참여자별 활동 내역 (participants 딕셔너리):")
            for user, info in self.participants.items():
                print(f"{user}: {info}")

    def calculate_scores(self) -> Dict:
        """Calculate participation scores"""
        scores = {}
        total_score_sum = 0

        for participant, activities in self.participants.items():
            # PR
            p_f = activities.get('p_enhancement', 0)
            p_b = activities.get('p_bug', 0)
            p_d = activities.get('p_documentation', 0)
            p_fb = p_f + p_b

            # 이슈
            i_f = activities.get('i_enhancement', 0)
            i_b = activities.get('i_bug', 0)
            i_d = activities.get('i_documentation', 0)
            i_fb = i_f + i_b

            # 점수 공식 (README 수식 준수)
            p_valid = p_fb + min(p_d, 3 * max(1, p_fb))
            i_valid = min(i_fb + i_d, 4 * p_valid)

            p_fb_at = min(p_fb, p_valid)
            p_d_at = p_valid - p_fb

            i_fb_at = min(i_fb, i_valid)
            i_d_at = i_valid - i_fb_at

            S = 3 * p_fb_at + 2 * p_d_at + 2 * i_fb_at + 1 * i_d_at

            scores[participant] = {
                "feat/bug PR": 3 * p_fb_at,
                "document PR": 2 * p_d_at,
                "feat/bug issue": 2 * i_fb_at,
                "document issue": 1 * i_d_at,
                "total": S
            }

            total_score_sum += S

        # 참여율 계산
        for participant in scores:
            total = scores[participant]["total"]
            rate = (total / total_score_sum) * 100 if total_score_sum > 0 else 0
            scores[participant]["rate"] = round(rate, 1)

        return dict(sorted(scores.items(), key=lambda x: x[1]["total"], reverse=True))

    def generate_table(self, scores: Dict, save_path) -> None:
        df = pd.DataFrame.from_dict(scores, orient="index")
        df.reset_index(inplace=True)
        df.rename(columns={"index": "name"}, inplace=True)
        df.to_csv(save_path, index=False)
        print(f"📊 CSV 결과 저장 완료: {save_path}")

    def generate_text(self, scores: Dict, save_path) -> None:
        table = PrettyTable()
        table.field_names = ["name", "feat/bug PR", "document PR", "feat/bug issue", "document issue", "total", "rate"]
        for name, score in scores.items():
            table.add_row(
                [name,
                 score["feat/bug PR"],
                 score["document PR"],
                 score["feat/bug issue"],
                 score["document issue"],
                 score["total"],
                 f'{score["rate"]:.1f}%']
            )

        with open(save_path, 'w') as txt_file:
            txt_file.write(str(table))
        print(f"📝 텍스트 결과 저장 완료: {save_path}")

    def generate_chart(self, scores: Dict, save_path: str = "results") -> None:
        sorted_scores = sorted([(key, value.get('total', 0)) for (key, value) in scores.items()], key=lambda item: item[1], reverse=True)
        participants, scores_sorted = zip(*sorted_scores) if sorted_scores else ([], [])

        num_participants = len(participants)
        height = max(3., num_participants * 0.2)

        plt.figure(figsize=(10, height))
        bars = plt.barh(participants, scores_sorted, height=0.5)

        plt.xlabel('Participation Score')
        plt.title('Repository Participation Scores')
        plt.suptitle(f"Total Participants: {num_participants}", fontsize=10, x=0.98, ha='right')
        plt.gca().invert_yaxis()

        for bar in bars:
            plt.text(
                bar.get_width() + 0.2,
                bar.get_y() + bar.get_height(),
                f'{bar.get_width():.1f}',
                va='center',
                fontsize=9
            )

        plt.tight_layout(pad=2)
        plt.savefig(save_path)
        print(f"📈 차트 저장 완료: {save_path}")