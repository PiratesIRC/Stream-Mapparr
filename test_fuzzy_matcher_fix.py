#!/usr/bin/env python3
"""
Test script to verify the fuzzy matcher bug fix.
Tests that streams which normalize to empty strings don't produce false positive matches.
"""

import sys
import os

# Add the Stream-Mapparr directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Stream-Mapparr'))

from fuzzy_matcher import FuzzyMatcher

def test_empty_string_normalization():
    """Test that empty normalized strings don't cause false positive matches."""
    print("=" * 80)
    print("Test 1: Empty String Normalization")
    print("=" * 80)

    # Create a fuzzy matcher
    matcher = FuzzyMatcher(plugin_dir=None, match_threshold=85)

    # Test case 1: Stream names that could normalize to empty strings
    test_streams = [
        "BR TNT SD",
        "BR MTV SD",
        "BR GNT SD",
        "BR TLC SD",
        "BR BIS SD"
    ]

    # Configure user_ignored_tags that would strip channel names
    user_ignored_tags = ["TNT", "MTV", "GNT", "TLC", "BIS", "SD", "HD"]

    # Test normalizing these streams
    print("\nNormalizing streams with aggressive tags:")
    for stream in test_streams:
        normalized = matcher.normalize_name(stream, user_ignored_tags, remove_country_prefix=True)
        print(f"  '{stream}' -> '{normalized}' (len={len(normalized)})")

    print("\n" + "-" * 80)

    # Test matching "GNT" channel against these streams
    channel_name = "GNT"
    print(f"\nAttempting to match channel '{channel_name}' against test streams...")

    matched_name, score, match_type = matcher.fuzzy_match(
        channel_name,
        test_streams,
        user_ignored_tags,
        remove_cinemax=False
    )

    if matched_name:
        print(f"✓ Match found: '{matched_name}' with score {score} (type: {match_type})")
        # Should only match "BR GNT SD"
        if matched_name == "BR GNT SD":
            print("✓ PASS: Matched the correct stream!")
        else:
            print(f"✗ FAIL: Matched wrong stream! Expected 'BR GNT SD', got '{matched_name}'")
            return False
    else:
        print(f"✗ No match found (score: {score})")
        print("  This could be acceptable if all streams normalize to empty strings")

    print()
    return True


def test_empty_string_similarity():
    """Test that empty strings don't match each other with 100% score."""
    print("=" * 80)
    print("Test 2: Empty String Similarity")
    print("=" * 80)

    matcher = FuzzyMatcher(plugin_dir=None, match_threshold=85)

    # Test empty string comparison
    score1 = matcher.calculate_similarity("", "")
    print(f"\nSimilarity('', '') = {score1}")

    if score1 == 0.0:
        print("✓ PASS: Empty strings return 0.0 similarity (no false positive match)")
    else:
        print(f"✗ FAIL: Empty strings return {score1} similarity (should be 0.0)")
        return False

    # Test empty vs non-empty
    score2 = matcher.calculate_similarity("", "test")
    print(f"Similarity('', 'test') = {score2}")

    if score2 == 0.0:
        print("✓ PASS: Empty string vs non-empty returns 0.0")
    else:
        print(f"✗ FAIL: Empty vs non-empty returns {score2} (should be 0.0)")
        return False

    print()
    return True


def test_valid_matches_still_work():
    """Test that legitimate matches still work after the fix."""
    print("=" * 80)
    print("Test 3: Valid Matches Still Work")
    print("=" * 80)

    matcher = FuzzyMatcher(plugin_dir=None, match_threshold=85)

    test_cases = [
        {
            "channel": "CNN",
            "streams": ["CNN HD", "CNN SD", "Fox News HD"],
            "expected": "CNN HD",
            "user_tags": []
        },
        {
            "channel": "HBO",
            "streams": ["HBO East HD", "HBO West SD", "Showtime HD"],
            "expected": "HBO East HD",
            "user_tags": []
        },
        {
            "channel": "ESPN",
            "streams": ["ESPN HD", "ESPN2 HD", "Fox Sports HD"],
            "expected": "ESPN HD",
            "user_tags": []
        }
    ]

    all_passed = True

    for i, test in enumerate(test_cases, 1):
        channel = test["channel"]
        streams = test["streams"]
        expected = test["expected"]
        user_tags = test["user_tags"]

        print(f"\nTest case {i}: Matching '{channel}' against {streams}")

        matched_name, score, match_type = matcher.fuzzy_match(
            channel,
            streams,
            user_tags,
            remove_cinemax=False
        )

        if matched_name == expected:
            print(f"✓ PASS: Matched '{matched_name}' (score: {score}, type: {match_type})")
        else:
            print(f"✗ FAIL: Expected '{expected}', got '{matched_name}'")
            all_passed = False

    print()
    return all_passed


def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("FUZZY MATCHER BUG FIX VERIFICATION")
    print("=" * 80 + "\n")

    results = []

    # Run tests
    results.append(("Empty String Similarity", test_empty_string_similarity()))
    results.append(("Empty String Normalization", test_empty_string_normalization()))
    results.append(("Valid Matches Still Work", test_valid_matches_still_work()))

    # Print summary
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")

    all_passed = all(passed for _, passed in results)

    print("\n" + "=" * 80)
    if all_passed:
        print("✓ ALL TESTS PASSED!")
    else:
        print("✗ SOME TESTS FAILED")
    print("=" * 80 + "\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
