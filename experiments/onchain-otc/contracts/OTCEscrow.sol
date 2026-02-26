// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title OTCEscrow — Atomic ERC-1155 ↔ ERC-20 swap
/// @notice Trustless P2P OTC for Polymarket conditional tokens.
///
/// Flow:
///   1. Maker creates an offer: deposits ERC-1155 tokens (e.g. YES shares)
///   2. Taker fills: deposits ERC-20 (USDC.e) → contract atomically swaps both sides
///   3. If deadline passes without fill, maker can cancel and reclaim tokens
///
/// Supports multiple concurrent offers via offer IDs.

interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

interface IERC1155 {
    function safeTransferFrom(address from, address to, uint256 id, uint256 amount, bytes calldata data) external;
    function balanceOf(address owner, uint256 id) external view returns (uint256);
}

contract OTCEscrow {
    struct Offer {
        address maker;           // depositor of ERC-1155 tokens
        address taker;           // authorized counterparty (address(0) = open to anyone)
        address erc1155Token;    // CTF contract address
        uint256 tokenId;         // ERC-1155 token ID (YES or NO share)
        uint256 tokenAmount;     // amount of ERC-1155 tokens offered
        address erc20Token;      // payment token (USDC.e)
        uint256 price;           // total ERC-20 amount requested
        uint256 deadline;        // unix timestamp; 0 = no expiry
        bool active;             // false once filled or cancelled
    }

    uint256 public nextOfferId;
    mapping(uint256 => Offer) public offers;

    event OfferCreated(uint256 indexed offerId, address indexed maker, uint256 tokenId, uint256 tokenAmount, uint256 price, uint256 deadline);
    event OfferFilled(uint256 indexed offerId, address indexed taker);
    event OfferCancelled(uint256 indexed offerId);

    /// @notice Create an offer by depositing ERC-1155 tokens into escrow.
    /// @dev Caller must have approved this contract via setApprovalForAll on the ERC-1155.
    function createOffer(
        address _erc1155Token,
        uint256 _tokenId,
        uint256 _tokenAmount,
        address _erc20Token,
        uint256 _price,
        address _taker,
        uint256 _deadline
    ) external returns (uint256 offerId) {
        require(_tokenAmount > 0, "zero amount");
        require(_price > 0, "zero price");

        offerId = nextOfferId++;
        offers[offerId] = Offer({
            maker: msg.sender,
            taker: _taker,
            erc1155Token: _erc1155Token,
            tokenId: _tokenId,
            tokenAmount: _tokenAmount,
            erc20Token: _erc20Token,
            price: _price,
            deadline: _deadline,
            active: true
        });

        // Pull ERC-1155 tokens from maker into escrow
        IERC1155(_erc1155Token).safeTransferFrom(msg.sender, address(this), _tokenId, _tokenAmount, "");

        emit OfferCreated(offerId, msg.sender, _tokenId, _tokenAmount, _price, _deadline);
    }

    /// @notice Fill an offer: pay ERC-20 and receive ERC-1155 tokens atomically.
    /// @dev Caller must have approved this contract to spend `price` of the ERC-20.
    function fillOffer(uint256 _offerId) external {
        Offer storage offer = offers[_offerId];
        require(offer.active, "not active");
        require(offer.deadline == 0 || block.timestamp <= offer.deadline, "expired");
        require(offer.taker == address(0) || offer.taker == msg.sender, "not authorized");

        offer.active = false;

        // Pull ERC-20 from taker to maker
        require(
            IERC20(offer.erc20Token).transferFrom(msg.sender, offer.maker, offer.price),
            "erc20 transfer failed"
        );

        // Send escrowed ERC-1155 tokens to taker
        IERC1155(offer.erc1155Token).safeTransferFrom(address(this), msg.sender, offer.tokenId, offer.tokenAmount, "");

        emit OfferFilled(_offerId, msg.sender);
    }

    /// @notice Cancel an expired or unwanted offer. Only maker can cancel.
    function cancelOffer(uint256 _offerId) external {
        Offer storage offer = offers[_offerId];
        require(offer.active, "not active");
        require(offer.maker == msg.sender, "not maker");

        offer.active = false;

        // Return escrowed ERC-1155 tokens to maker
        IERC1155(offer.erc1155Token).safeTransferFrom(address(this), msg.sender, offer.tokenId, offer.tokenAmount, "");

        emit OfferCancelled(_offerId);
    }

    /// @notice View full offer details.
    function getOffer(uint256 _offerId) external view returns (Offer memory) {
        return offers[_offerId];
    }

    /// @dev Required to receive ERC-1155 tokens.
    function onERC1155Received(address, address, uint256, uint256, bytes calldata) external pure returns (bytes4) {
        return this.onERC1155Received.selector;
    }

    /// @dev Required to receive batch ERC-1155 tokens.
    function onERC1155BatchReceived(address, address, uint256[] calldata, uint256[] calldata, bytes calldata) external pure returns (bytes4) {
        return this.onERC1155BatchReceived.selector;
    }

    /// @dev ERC-165 support for ERC1155Receiver.
    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == 0x4e2312e0; // ERC1155Receiver
    }
}
