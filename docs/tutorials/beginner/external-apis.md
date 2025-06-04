# Working with External APIs

Learn how to integrate external APIs into your Flux workflows with proper error handling, authentication, and data transformation.

## Overview

External API integration is a common requirement in data processing workflows. This tutorial demonstrates best practices for making HTTP requests, handling authentication, processing responses, and managing API-related errors in Flux workflows.

## Prerequisites

- Complete the [First Data Pipeline](first-data-pipeline.md) tutorial
- Basic understanding of HTTP APIs and JSON
- Familiarity with Python's `requests` library

## What You'll Build

We'll create a workflow that:
1. Fetches user data from a REST API
2. Processes and enriches the data
3. Handles rate limiting and authentication
4. Implements retry logic for network failures
5. Transforms and stores the results

## Step 1: Setup Dependencies

First, create a new directory and install required packages:

```bash
mkdir flux-api-tutorial
cd flux-api-tutorial
pip install flux-engine requests
```

Create a `requirements.txt` file:

```txt
flux-engine>=0.1.0
requests>=2.31.0
pandas>=2.0.0
```

## Step 2: Basic API Integration

Let's start with a simple task that fetches data from a public API:

```python
# api_workflow.py
import requests
from flux import task, workflow
from typing import Dict, List, Any
import time
import logging

@task(
    retry_count=3,
    retry_delay=1.0,
    timeout=30.0
)
def fetch_user_data(user_id: int) -> Dict[str, Any]:
    """Fetch user data from JSONPlaceholder API."""
    url = f"https://jsonplaceholder.typicode.com/users/{user_id}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch user {user_id}: {e}")
        raise

@task
def extract_user_info(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant user information."""
    return {
        'id': user_data['id'],
        'name': user_data['name'],
        'email': user_data['email'],
        'company': user_data['company']['name'],
        'city': user_data['address']['city'],
        'website': user_data.get('website', '')
    }

@workflow
def process_single_user(user_id: int):
    """Process a single user's data."""
    raw_data = fetch_user_data(user_id)
    processed_data = extract_user_info(raw_data)
    return processed_data
```

## Step 3: Handling Multiple API Calls

Now let's process multiple users in parallel:

```python
from flux.tasks import parallel

@task(
    retry_count=2,
    retry_delay=0.5
)
def fetch_user_posts(user_id: int) -> List[Dict[str, Any]]:
    """Fetch posts for a specific user."""
    url = f"https://jsonplaceholder.typicode.com/posts?userId={user_id}"

    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()

@task
def enrich_user_with_posts(user_data: Dict[str, Any], posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine user data with their posts."""
    return {
        **user_data,
        'post_count': len(posts),
        'latest_post': posts[0]['title'] if posts else 'No posts',
        'posts': [{'id': p['id'], 'title': p['title']} for p in posts[:3]]  # Latest 3 posts
    }

@workflow
def process_user_with_posts(user_id: int):
    """Process user data and their posts in parallel."""
    # Fetch user data and posts concurrently
    user_task = fetch_user_data(user_id)
    posts_task = fetch_user_posts(user_id)

    # Wait for both to complete
    user_data = extract_user_info(user_task)
    posts_data = posts_task

    # Combine the results
    enriched_data = enrich_user_with_posts(user_data, posts_data)
    return enriched_data
```

## Step 4: Rate Limiting and Authentication

Add proper rate limiting and authentication handling:

```python
import os
from datetime import datetime, timedelta
from threading import Lock

class RateLimiter:
    def __init__(self, max_calls: int, time_window: int):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []
        self.lock = Lock()

    def wait_if_needed(self):
        with self.lock:
            now = datetime.now()
            # Remove calls outside the time window
            self.calls = [call_time for call_time in self.calls
                         if now - call_time < timedelta(seconds=self.time_window)]

            if len(self.calls) >= self.max_calls:
                # Wait until the oldest call expires
                oldest_call = min(self.calls)
                wait_time = (oldest_call + timedelta(seconds=self.time_window) - now).total_seconds()
                if wait_time > 0:
                    time.sleep(wait_time)

            self.calls.append(now)

# Global rate limiter (100 calls per minute)
api_rate_limiter = RateLimiter(max_calls=100, time_window=60)

@task(
    retry_count=3,
    retry_delay=2.0,
    timeout=30.0
)
def fetch_github_user(username: str) -> Dict[str, Any]:
    """Fetch user data from GitHub API with authentication and rate limiting."""
    api_rate_limiter.wait_if_needed()

    headers = {}
    github_token = os.environ.get('GITHUB_TOKEN')
    if github_token:
        headers['Authorization'] = f'token {github_token}'

    url = f"https://api.github.com/users/{username}"

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 403 and 'rate limit' in response.text.lower():
            # Rate limit exceeded, wait and retry
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            if reset_time:
                wait_time = max(0, reset_time - int(time.time()))
                logging.warning(f"Rate limit exceeded, waiting {wait_time} seconds")
                time.sleep(wait_time)
                raise Exception("Rate limit exceeded, retrying...")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch GitHub user {username}: {e}")
        raise

@task
def process_github_user(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process GitHub user data."""
    return {
        'username': user_data['login'],
        'name': user_data.get('name', ''),
        'public_repos': user_data['public_repos'],
        'followers': user_data['followers'],
        'following': user_data['following'],
        'created_at': user_data['created_at'],
        'bio': user_data.get('bio', ''),
        'location': user_data.get('location', ''),
        'blog': user_data.get('blog', '')
    }

@workflow
def analyze_github_users(usernames: List[str]):
    """Analyze multiple GitHub users."""
    # Process users in parallel with rate limiting
    user_tasks = []
    for username in usernames:
        user_task = fetch_github_user(username)
        processed_task = process_github_user(user_task)
        user_tasks.append(processed_task)

    # Execute all tasks in parallel
    results = parallel(*user_tasks)
    return results
```

## Step 5: Error Handling and Fallbacks

Implement comprehensive error handling with fallback strategies:

```python
@task(
    retry_count=3,
    retry_delay=1.0,
    fallback=lambda username: {'username': username, 'status': 'unavailable', 'source': 'fallback'}
)
def fetch_user_with_fallback(username: str) -> Dict[str, Any]:
    """Fetch user data with fallback to cached data."""
    try:
        # Try primary API
        return fetch_github_user(username)
    except Exception as e:
        logging.warning(f"Primary API failed for {username}: {e}")

        # Try fallback API or cache
        try:
            return fetch_from_cache(username)
        except Exception as cache_error:
            logging.error(f"Cache also failed for {username}: {cache_error}")
            raise e  # Re-raise original error

@task
def fetch_from_cache(username: str) -> Dict[str, Any]:
    """Fetch user data from local cache."""
    cache_file = f"cache/{username}.json"

    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            import json
            cached_data = json.load(f)
            cached_data['source'] = 'cache'
            return cached_data

    raise FileNotFoundError(f"No cached data for {username}")

@task
def cache_user_data(username: str, user_data: Dict[str, Any]):
    """Cache user data for future use."""
    os.makedirs('cache', exist_ok=True)
    cache_file = f"cache/{username}.json"

    # Add timestamp to cached data
    user_data['cached_at'] = datetime.now().isoformat()

    with open(cache_file, 'w') as f:
        import json
        json.dump(user_data, f, indent=2)

@workflow
def robust_user_analysis(usernames: List[str]):
    """Analyze users with robust error handling and caching."""
    results = []

    for username in usernames:
        # Fetch user data with fallback
        user_data = fetch_user_with_fallback(username)

        # Process the data
        processed_data = process_github_user(user_data)

        # Cache successful results
        if user_data.get('source') != 'fallback':
            cache_user_data(username, processed_data)

        results.append(processed_data)

    return results
```

## Step 6: Data Transformation and Validation

Add data validation and transformation:

```python
from typing import Optional
from dataclasses import dataclass

@dataclass
class UserProfile:
    username: str
    name: Optional[str]
    public_repos: int
    followers: int
    following: int
    created_at: str
    bio: Optional[str]
    location: Optional[str]
    blog: Optional[str]
    activity_score: float = 0.0

@task
def validate_and_transform_user(user_data: Dict[str, Any]) -> UserProfile:
    """Validate and transform user data into structured format."""
    # Validate required fields
    required_fields = ['username', 'public_repos', 'followers', 'following', 'created_at']
    for field in required_fields:
        if field not in user_data or user_data[field] is None:
            raise ValueError(f"Missing required field: {field}")

    # Calculate activity score based on repos and followers
    repos = user_data['public_repos']
    followers = user_data['followers']
    activity_score = (repos * 2 + followers) / 10.0

    return UserProfile(
        username=user_data['username'],
        name=user_data.get('name'),
        public_repos=repos,
        followers=followers,
        following=user_data['following'],
        created_at=user_data['created_at'],
        bio=user_data.get('bio'),
        location=user_data.get('location'),
        blog=user_data.get('blog'),
        activity_score=round(activity_score, 2)
    )

@task
def generate_user_report(users: List[UserProfile]) -> Dict[str, Any]:
    """Generate summary report from user profiles."""
    if not users:
        return {'error': 'No users to analyze'}

    total_users = len(users)
    total_repos = sum(user.public_repos for user in users)
    total_followers = sum(user.followers for user in users)
    avg_activity = sum(user.activity_score for user in users) / total_users

    # Find top users
    top_by_repos = sorted(users, key=lambda x: x.public_repos, reverse=True)[:3]
    top_by_followers = sorted(users, key=lambda x: x.followers, reverse=True)[:3]

    return {
        'summary': {
            'total_users': total_users,
            'total_repositories': total_repos,
            'total_followers': total_followers,
            'average_activity_score': round(avg_activity, 2)
        },
        'top_by_repositories': [{'username': u.username, 'repos': u.public_repos} for u in top_by_repos],
        'top_by_followers': [{'username': u.username, 'followers': u.followers} for u in top_by_followers],
        'locations': list(set(user.location for user in users if user.location))
    }

@workflow
def complete_user_analysis(usernames: List[str]):
    """Complete user analysis workflow with validation and reporting."""
    # Fetch and process users
    raw_users = robust_user_analysis(usernames)

    # Validate and transform each user
    validated_users = []
    for user_data in raw_users:
        try:
            validated_user = validate_and_transform_user(user_data)
            validated_users.append(validated_user)
        except ValueError as e:
            logging.error(f"Validation failed for user {user_data.get('username', 'unknown')}: {e}")

    # Generate report
    report = generate_user_report(validated_users)

    return {
        'users': [user.__dict__ for user in validated_users],
        'report': report,
        'processed_at': datetime.now().isoformat()
    }
```

## Step 7: Testing the Workflow

Create comprehensive tests for your API workflow:

```python
# test_api_workflow.py
import pytest
from unittest.mock import patch, Mock
import json
from api_workflow import (
    fetch_user_data, extract_user_info, fetch_github_user,
    process_github_user, validate_and_transform_user, UserProfile
)

class TestAPIWorkflow:
    def test_extract_user_info(self):
        """Test user info extraction."""
        mock_user_data = {
            'id': 1,
            'name': 'John Doe',
            'email': 'john@example.com',
            'company': {'name': 'Acme Corp'},
            'address': {'city': 'New York'},
            'website': 'https://johndoe.com'
        }

        result = extract_user_info(mock_user_data)

        assert result['id'] == 1
        assert result['name'] == 'John Doe'
        assert result['email'] == 'john@example.com'
        assert result['company'] == 'Acme Corp'
        assert result['city'] == 'New York'
        assert result['website'] == 'https://johndoe.com'

    @patch('api_workflow.requests.get')
    def test_fetch_user_data_success(self, mock_get):
        """Test successful user data fetch."""
        mock_response = Mock()
        mock_response.json.return_value = {'id': 1, 'name': 'Test User'}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_user_data(1)

        assert result == {'id': 1, 'name': 'Test User'}
        mock_get.assert_called_once_with(
            'https://jsonplaceholder.typicode.com/users/1',
            timeout=10
        )

    @patch('api_workflow.requests.get')
    def test_fetch_user_data_failure(self, mock_get):
        """Test failed user data fetch."""
        mock_get.side_effect = Exception("Network error")

        with pytest.raises(Exception):
            fetch_user_data(1)

    def test_validate_and_transform_user_success(self):
        """Test successful user validation and transformation."""
        user_data = {
            'username': 'testuser',
            'name': 'Test User',
            'public_repos': 10,
            'followers': 50,
            'following': 30,
            'created_at': '2020-01-01T00:00:00Z',
            'bio': 'Test bio',
            'location': 'Test City',
            'blog': 'https://testblog.com'
        }

        result = validate_and_transform_user(user_data)

        assert isinstance(result, UserProfile)
        assert result.username == 'testuser'
        assert result.public_repos == 10
        assert result.activity_score == 7.0  # (10*2 + 50) / 10

    def test_validate_and_transform_user_missing_field(self):
        """Test user validation with missing required field."""
        user_data = {
            'username': 'testuser',
            # Missing required fields
        }

        with pytest.raises(ValueError, match="Missing required field"):
            validate_and_transform_user(user_data)

def test_integration():
    """Integration test with real API (if GITHUB_TOKEN is available)."""
    import os

    if not os.environ.get('GITHUB_TOKEN'):
        pytest.skip("GITHUB_TOKEN not available")

    # Test with a well-known GitHub user
    user_data = fetch_github_user('octocat')
    assert user_data['login'] == 'octocat'

    processed = process_github_user(user_data)
    assert processed['username'] == 'octocat'

    validated = validate_and_transform_user(processed)
    assert isinstance(validated, UserProfile)
    assert validated.username == 'octocat'
```

## Step 8: Running the Workflow

Create a main script to run your workflow:

```python
# main.py
import os
import logging
from api_workflow import complete_user_analysis

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def main():
    # Set GitHub token if available
    github_token = os.environ.get('GITHUB_TOKEN')
    if not github_token:
        print("Warning: GITHUB_TOKEN not set. API requests will be rate limited.")

    # Example users to analyze
    usernames = ['octocat', 'torvalds', 'gvanrossum', 'kentcdodds', 'sindresorhus']

    print(f"Analyzing {len(usernames)} GitHub users...")

    try:
        # Execute the workflow
        result = complete_user_analysis(usernames)

        # Display results
        print("\n=== Analysis Complete ===")
        print(f"Processed {len(result['users'])} users")
        print(f"Total repositories: {result['report']['summary']['total_repositories']}")
        print(f"Total followers: {result['report']['summary']['total_followers']}")
        print(f"Average activity score: {result['report']['summary']['average_activity_score']}")

        print("\nTop users by repositories:")
        for user in result['report']['top_by_repositories']:
            print(f"  {user['username']}: {user['repos']} repos")

        print("\nTop users by followers:")
        for user in result['report']['top_by_followers']:
            print(f"  {user['username']}: {user['followers']} followers")

        # Save results to file
        import json
        with open('user_analysis_results.json', 'w') as f:
            json.dump(result, f, indent=2)

        print(f"\nDetailed results saved to user_analysis_results.json")

    except Exception as e:
        logging.error(f"Workflow failed: {e}")
        raise

if __name__ == "__main__":
    main()
```

## Step 9: Environment Setup

Create environment configuration files:

```bash
# .env (for local development)
GITHUB_TOKEN=your_github_token_here
LOG_LEVEL=INFO
API_TIMEOUT=30
MAX_RETRIES=3
```

```python
# config.py
import os
from dataclasses import dataclass

@dataclass
class APIConfig:
    github_token: str = os.environ.get('GITHUB_TOKEN', '')
    log_level: str = os.environ.get('LOG_LEVEL', 'INFO')
    api_timeout: int = int(os.environ.get('API_TIMEOUT', '30'))
    max_retries: int = int(os.environ.get('MAX_RETRIES', '3'))
    rate_limit_calls: int = int(os.environ.get('RATE_LIMIT_CALLS', '100'))
    rate_limit_window: int = int(os.environ.get('RATE_LIMIT_WINDOW', '60'))

# Global configuration instance
config = APIConfig()
```

## Running the Complete Example

1. **Set up environment:**
   ```bash
   export GITHUB_TOKEN="your_token_here"
   pip install -r requirements.txt
   ```

2. **Run the workflow:**
   ```bash
   python main.py
   ```

3. **Run tests:**
   ```bash
   pytest test_api_workflow.py -v
   ```

## Best Practices Demonstrated

This tutorial demonstrates several important patterns:

### 1. Rate Limiting
- Implemented proper rate limiting to respect API limits
- Graceful handling of rate limit responses
- Automatic waiting and retry logic

### 2. Authentication
- Secure token handling via environment variables
- Optional authentication for public APIs
- Header management for authenticated requests

### 3. Error Handling
- Comprehensive retry strategies
- Fallback mechanisms with cached data
- Graceful degradation when services are unavailable

### 4. Data Validation
- Strong typing with dataclasses
- Input validation and sanitization
- Structured error reporting

### 5. Parallel Processing
- Concurrent API calls where possible
- Proper synchronization of dependent tasks
- Resource-aware parallel execution

### 6. Monitoring and Observability
- Structured logging throughout the workflow
- Performance metrics and timing
- Error tracking and reporting

## Production Considerations

When deploying API-integrated workflows to production:

1. **Secrets Management**: Use Flux's secrets management for API tokens
2. **Monitoring**: Set up alerts for API failures and rate limiting
3. **Caching**: Implement distributed caching for frequently accessed data
4. **Circuit Breakers**: Add circuit breaker patterns for unreliable APIs
5. **Documentation**: Document API dependencies and failure modes

## Next Steps

- Explore the [Multi-Step Data Processing](../intermediate/multi-step-processing.md) tutorial
- Learn about [State Management](../intermediate/state-management.md) for long-running workflows
- Check out [Performance Optimization](../advanced/performance-optimization.md) techniques

## Summary

You've learned how to:
- Integrate external APIs into Flux workflows
- Handle authentication and rate limiting
- Implement comprehensive error handling and fallbacks
- Process and validate API responses
- Create robust, production-ready API workflows

The patterns demonstrated here can be applied to any external API integration, from simple data fetching to complex multi-service orchestration workflows.
