"""Load synthetic examples through the public API. Safe to run more than once."""

import argparse
import os

import httpx

CLIENTS = [
    {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@neviswealth.com",
        "description": "Business owner interested in retirement planning.",
        "documents": [
            {
                "title": "Utility bill",
                "content": (
                    "Monthly electricity bill for John Doe, 14 Oak Street, London. "
                    "Account balance GBP 82.40. Billing period September 2026."
                ),
            },
            {
                "title": "Pension review",
                "content": (
                    "Retirement savings and pension contribution projections for the next "
                    "twenty years. The client plans to stop working at age 65."
                ),
            },
        ],
    },
    {
        "first_name": "Maya",
        "last_name": "Patel",
        "email": "maya.patel@example.com",
        "description": "Software engineer saving for a first home.",
        "documents": [
            {
                "title": "Passport",
                "content": "A valid passport confirming identity, nationality and date of birth.",
            },
            {
                "title": "Payslip",
                "content": "Monthly payslip with gross earnings, tax deductions and net wages.",
            },
        ],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
    with httpx.Client(base_url=args.url, headers=headers, timeout=60) as api:
        for example in CLIENTS:
            payload = {key: value for key, value in example.items() if key != "documents"}
            response = api.post("/clients", json=payload)
            if response.status_code == 409:
                existing = api.get("/search", params={"q": example["email"]})
                existing.raise_for_status()
                client = next(
                    hit["client"]
                    for hit in existing.json()
                    if hit["type"] == "client" and hit["client"]["email"] == example["email"]
                )
            else:
                response.raise_for_status()
                client = response.json()
            for document in example["documents"]:
                response = api.get(
                    "/search", params={"q": document["title"], "client_id": client["id"]}
                )
                response.raise_for_status()
                if any(
                    hit["type"] == "document" and hit["document"]["title"] == document["title"]
                    for hit in response.json()
                ):
                    continue
                response = api.post(f"/clients/{client['id']}/documents", json=document)
                response.raise_for_status()
            print(f"Ready: {client['first_name']} {client['last_name']}")


if __name__ == "__main__":
    main()
