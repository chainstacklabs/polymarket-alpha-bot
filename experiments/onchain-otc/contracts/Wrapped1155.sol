// SPDX-License-Identifier: LGPL-3.0
pragma solidity ^0.8.20;

/// @title Wrapped1155 — ERC-20 wrapper for a single ERC-1155 token ID
/// @notice Minimal implementation following Gnosis 1155-to-20 pattern.
///         Each instance wraps exactly one (multiToken, tokenId) pair.
contract Wrapped1155 {
    string public name;
    string public symbol;
    uint8 public constant decimals = 6; // match USDC.e / CTF token precision

    address public immutable factory;
    address public immutable multiToken;
    uint256 public immutable tokenId;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(address _multiToken, uint256 _tokenId, string memory _name, string memory _symbol) {
        factory = msg.sender;
        multiToken = _multiToken;
        tokenId = _tokenId;
        name = _name;
        symbol = _symbol;
    }

    function mint(address to, uint256 amount) external {
        require(msg.sender == factory, "only factory");
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function burn(address from, uint256 amount) external {
        require(msg.sender == factory, "only factory");
        balanceOf[from] -= amount;
        totalSupply -= amount;
        emit Transfer(from, address(0), amount);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(to != address(0), "zero address");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(to != address(0), "zero address");
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
        return true;
    }
}

interface IERC1155 {
    function safeTransferFrom(address from, address to, uint256 id, uint256 amount, bytes calldata data) external;
}

/// @title Wrapped1155Factory — Creates ERC-20 wrappers for ERC-1155 tokens
/// @notice Wrap by transferring ERC-1155 to this factory (onERC1155Received
///         callback deploys wrapper + mints ERC-20). Unwrap burns ERC-20 and
///         returns the underlying ERC-1155 tokens.
contract Wrapped1155Factory {
    /// @dev key = keccak256(multiToken, tokenId) => deployed Wrapped1155 address
    mapping(bytes32 => address) public wrappers;

    event Wrapped(address indexed multiToken, uint256 indexed tokenId, address wrapper, address indexed to, uint256 amount);
    event Unwrapped(address indexed multiToken, uint256 indexed tokenId, address indexed to, uint256 amount);

    function getWrapped(address multiToken, uint256 tokenId) external view returns (address) {
        return wrappers[keccak256(abi.encodePacked(multiToken, tokenId))];
    }

    /// @notice Unwrap: burn ERC-20, return ERC-1155 tokens
    function unwrap(address multiToken, uint256 tokenId, uint256 amount, address to) external {
        bytes32 key = keccak256(abi.encodePacked(multiToken, tokenId));
        address wrapper = wrappers[key];
        require(wrapper != address(0), "not wrapped");

        Wrapped1155(wrapper).burn(msg.sender, amount);
        IERC1155(multiToken).safeTransferFrom(address(this), to, tokenId, amount, "");

        emit Unwrapped(multiToken, tokenId, to, amount);
    }

    /// @notice Called when ERC-1155 tokens are transferred to this contract.
    ///         THIS IS THE WRAP FUNCTION — just safeTransferFrom your tokens here.
    /// @dev First wrap for a new tokenId costs ~650k gas (deploys wrapper).
    ///      Subsequent wraps cost ~57k gas. Provide sufficient gas limit.
    function onERC1155Received(
        address,
        address from,
        uint256 id,
        uint256 value,
        bytes calldata
    ) external returns (bytes4) {
        address multiToken = msg.sender;
        bytes32 key = keccak256(abi.encodePacked(multiToken, id));

        address wrapper = wrappers[key];
        if (wrapper == address(0)) {
            wrapper = address(new Wrapped1155(multiToken, id, "Wrapped CTF Token", "wCTF"));
            wrappers[key] = wrapper;
        }

        Wrapped1155(wrapper).mint(from, value);
        emit Wrapped(multiToken, id, wrapper, from, value);

        return this.onERC1155Received.selector;
    }

    function onERC1155BatchReceived(
        address, address, uint256[] calldata, uint256[] calldata, bytes calldata
    ) external pure returns (bytes4) {
        revert("use single transfer");
    }

    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == 0x4e2312e0; // ERC1155Receiver
    }
}
