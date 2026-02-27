"""
Configuration manager with keyring integration for secure credential storage.
"""

import json
from pathlib import Path
from typing import Optional
import keyring
from keyring.errors import KeyringError

from codewiki.cli.models.config import Configuration
from codewiki.cli.utils.errors import ConfigurationError, FileSystemError
from codewiki.cli.utils.fs import ensure_directory, safe_write, safe_read


# Keyring configuration
KEYRING_SERVICE = "codewiki"
KEYRING_API_KEY_ACCOUNT = "api_key"

# Configuration file location
CONFIG_DIR = Path.home() / ".codewiki"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_VERSION = "1.0"


class ConfigManager:
    """
    Manages CodeWiki configuration with secure keyring storage for API keys.
    
    Storage:
        - API key: System keychain via keyring (macOS Keychain, Windows Credential Manager, 
                  Linux Secret Service)
        - Other settings: ~/.codewiki/config.json
    """
    
    def __init__(self):
        """Initialize the configuration manager."""
        self._api_key: Optional[str] = None
        self._config: Optional[Configuration] = None
        self._keyring_available = self._check_keyring_available()
    
    def _check_keyring_available(self) -> bool:
        """Check if system keyring is available."""
        try:
            # Try to get/set a test value
            keyring.get_password(KEYRING_SERVICE, "__test__")
            return True
        except Exception:
            return False
    
    def load(self) -> bool:
        """
        Load configuration from file and keyring.
        
        Returns:
            True if configuration exists, False otherwise
        """
        # Load from JSON file
        if not CONFIG_FILE.exists():
            return False
        
        try:
            content = safe_read(CONFIG_FILE)
            data = json.loads(content)
            
            # Validate version
            if data.get('version') != CONFIG_VERSION:
                # Could implement migration here
                pass
            
            self._config = Configuration.from_dict(data)

            # Load API key: try keyring first, fallback to config file
            try:
                self._api_key = keyring.get_password(KEYRING_SERVICE, KEYRING_API_KEY_ACCOUNT)
            except Exception:
                self._keyring_available = False

            if self._api_key is None:
                self._api_key = data.get("api_key")
            
            return True
        except (json.JSONDecodeError, FileSystemError) as e:
            raise ConfigurationError(f"Failed to load configuration: {e}")
    
    def save(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        main_model: Optional[str] = None,
        cluster_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        long_context_model: Optional[str] = None,
        default_output: Optional[str] = None,
        max_tokens: Optional[int] = None,
        max_token_per_module: Optional[int] = None,
        max_token_per_leaf_module: Optional[int] = None,
        max_depth: Optional[int] = None,
        max_concurrent: Optional[int] = None,
        output_language: Optional[str] = None,
    ):
        """
        Save configuration to file and keyring.
        
        Args:
            api_key: API key (stored in keyring)
            base_url: LLM API base URL
            main_model: Primary model
            cluster_model: Clustering model
            fallback_model: Fallback model
            default_output: Default output directory
            max_tokens: Maximum tokens for LLM response
            max_token_per_module: Maximum tokens per module for clustering
            max_token_per_leaf_module: Maximum tokens per leaf module
            max_depth: Maximum depth for hierarchical decomposition
        """
        # Ensure config directory exists
        try:
            ensure_directory(CONFIG_DIR)
        except FileSystemError as e:
            raise ConfigurationError(f"Cannot create config directory: {e}")
        
        # Load existing config or create new
        if self._config is None:
            if CONFIG_FILE.exists():
                self.load()
            else:
                from codewiki.cli.models.config import AgentInstructions
                self._config = Configuration(
                    base_url="",
                    main_model="",
                    cluster_model="",
                    fallback_model="glm-4p5",
                    default_output="docs",
                    agent_instructions=AgentInstructions()
                )
        
        # Update fields if provided
        if base_url is not None:
            self._config.base_url = base_url
        if main_model is not None:
            self._config.main_model = main_model
        if cluster_model is not None:
            self._config.cluster_model = cluster_model
        if fallback_model is not None:
            self._config.fallback_model = fallback_model
        if long_context_model is not None:
            self._config.long_context_model = long_context_model
        if default_output is not None:
            self._config.default_output = default_output
        if max_tokens is not None:
            self._config.max_tokens = max_tokens
        if max_token_per_module is not None:
            self._config.max_token_per_module = max_token_per_module
        if max_token_per_leaf_module is not None:
            self._config.max_token_per_leaf_module = max_token_per_leaf_module
        if max_depth is not None:
            self._config.max_depth = max_depth
        if max_concurrent is not None:
            self._config.max_concurrent = max_concurrent
        if output_language is not None:
            self._config.output_language = output_language
        
        # Validate configuration (only if base fields are set)
        if self._config.base_url and self._config.main_model and self._config.cluster_model:
            self._config.validate()
        
        # Save API key to keyring, fallback to config file
        if api_key is not None:
            self._api_key = api_key
            if self._keyring_available:
                try:
                    keyring.set_password(KEYRING_SERVICE, KEYRING_API_KEY_ACCOUNT, api_key)
                except Exception:
                    self._keyring_available = False

        # Save non-sensitive config to JSON (api_key included if keyring unavailable)
        config_data = {
            "version": CONFIG_VERSION,
            **self._config.to_dict()
        }
        if not self._keyring_available and self._api_key is not None:
            config_data["api_key"] = self._api_key
        
        try:
            safe_write(CONFIG_FILE, json.dumps(config_data, indent=2))
        except FileSystemError as e:
            raise ConfigurationError(f"Failed to save configuration: {e}")
    
    def get_api_key(self) -> Optional[str]:
        """
        Get API key from keyring.
        
        Returns:
            API key or None if not set
        """
        if self._api_key is None:
            try:
                self._api_key = keyring.get_password(KEYRING_SERVICE, KEYRING_API_KEY_ACCOUNT)
            except Exception:
                pass
        
        return self._api_key
    
    def get_config(self) -> Optional[Configuration]:
        """
        Get current configuration.
        
        Returns:
            Configuration object or None if not loaded
        """
        return self._config
    
    def is_configured(self) -> bool:
        """
        Check if configuration is complete and valid.
        
        Returns:
            True if configured, False otherwise
        """
        if self._config is None:
            return False
        
        # Check if API key is set
        if self.get_api_key() is None:
            return False
        
        # Check if config is complete
        return self._config.is_complete()
    
    def delete_api_key(self):
        """Delete API key from keyring."""
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_API_KEY_ACCOUNT)
            self._api_key = None
        except Exception:
            pass
    
    def clear(self):
        """Clear all configuration (file and keyring)."""
        # Delete API key from keyring
        self.delete_api_key()
        
        # Delete config file
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
        
        self._config = None
        self._api_key = None
    
    @property
    def keyring_available(self) -> bool:
        """Check if keyring is available."""
        return self._keyring_available
    
    @property
    def config_file_path(self) -> Path:
        """Get configuration file path."""
        return CONFIG_FILE

