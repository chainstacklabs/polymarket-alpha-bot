// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IntentSettlement — Permissionless atomic ERC-20 swap via signed intents
/// @notice No operator, no whitelist, no deposit. Seller signs an EIP-712 intent
///         off-chain, ANY solver can fill it on-chain. The contract verifies the
///         signature and executes both transfers atomically.
///
/// Flow:
///   1. Seller signs intent off-chain: "sell X wYES for ≥Y USDC.e"
///   2. Seller approves this contract to spend wYES
///   3. Solver calls fillIntent(intent, signature) — contract:
///      a. Verifies EIP-712 signature via ecrecover
///      b. Pulls wYES from seller → solver
///      c. Pulls USDC from solver → seller
///      d. All atomic — either both succeed or both revert
///
/// Compared to Polymarket CLOB:
///   CLOB:   operator-gated matchOrders() — only whitelisted EOAs
///   This:   permissionless fillIntent()  — anyone can be a solver

interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

contract IntentSettlement {
    struct SellIntent {
        address seller;
        address sellToken;
        uint256 sellAmount;
        address buyToken;
        uint256 minBuyAmount;
        uint256 deadline;
        uint256 nonce;
    }

    bytes32 public constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");

    bytes32 public constant INTENT_TYPEHASH =
        keccak256(
            "SellIntent(address seller,address sellToken,uint256 sellAmount,address buyToken,uint256 minBuyAmount,uint256 deadline,uint256 nonce)"
        );

    bytes32 public immutable DOMAIN_SEPARATOR;

    /// @dev Nonce per seller — increment to cancel all pending intents
    mapping(address => uint256) public nonces;

    /// @dev Prevent double-fill of the same intent
    mapping(bytes32 => bool) public filled;

    event IntentFilled(
        bytes32 indexed intentHash,
        address indexed seller,
        address indexed solver,
        address sellToken,
        uint256 sellAmount,
        uint256 buyAmount
    );

    event IntentCancelled(address indexed seller, uint256 newNonce);

    constructor() {
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                keccak256("CTF Intent Exchange"),
                keccak256("1"),
                block.chainid,
                address(this)
            )
        );
    }

    /// @notice Fill a signed sell intent. Anyone can call this — permissionless.
    /// @param intent   The signed intent struct
    /// @param buyAmount Actual amount solver is paying (>= minBuyAmount)
    /// @param v        Signature v
    /// @param r        Signature r
    /// @param s        Signature s
    function fillIntent(SellIntent calldata intent, uint256 buyAmount, uint8 v, bytes32 r, bytes32 s) external {
        require(block.timestamp <= intent.deadline, "intent expired");
        require(buyAmount >= intent.minBuyAmount, "below minimum price");
        require(nonces[intent.seller] == intent.nonce, "invalid nonce");

        // Compute EIP-712 digest
        bytes32 structHash = keccak256(
            abi.encode(
                INTENT_TYPEHASH,
                intent.seller,
                intent.sellToken,
                intent.sellAmount,
                intent.buyToken,
                intent.minBuyAmount,
                intent.deadline,
                intent.nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));

        // Verify signature on-chain
        address recovered = ecrecover(digest, v, r, s);
        require(recovered != address(0) && recovered == intent.seller, "invalid signature");

        // Prevent replay
        require(!filled[digest], "already filled");
        filled[digest] = true;
        nonces[intent.seller] = intent.nonce + 1;

        // Atomic swap: both transfers or revert
        require(
            IERC20(intent.sellToken).transferFrom(intent.seller, msg.sender, intent.sellAmount),
            "sell transfer failed"
        );
        require(
            IERC20(intent.buyToken).transferFrom(msg.sender, intent.seller, buyAmount), "buy transfer failed"
        );

        emit IntentFilled(digest, intent.seller, msg.sender, intent.sellToken, intent.sellAmount, buyAmount);
    }

    /// @notice Cancel all pending intents by incrementing nonce.
    function cancelAll() external {
        nonces[msg.sender]++;
        emit IntentCancelled(msg.sender, nonces[msg.sender]);
    }
}
