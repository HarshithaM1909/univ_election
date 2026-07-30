# core/blockchain_utils.py

from django.conf import settings
from web3 import Web3
import time

# Guard the POA middleware import for compatibility across web3.py versions
try:
    from web3.middleware import ExtraDataToPOAMiddleware
    _has_poa_middleware = True
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware
        _has_poa_middleware = True
    except ImportError:
        _has_poa_middleware = False
        print("WARNING: Could not import POA middleware. POA chain support may be unavailable.")

def send_vote_to_blockchain(student_id: str):
    """
    Connects to the blockchain, hashes the student ID, and sends a transaction
    to the VoteLedger smart contract to record the vote.
    """
    try:
        # 1. Connect to the blockchain network
        w3 = Web3(Web3.HTTPProvider(settings.BLOCKCHAIN_RPC_URL))

        if _has_poa_middleware:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not w3.is_connected():
            print("Error: Could not connect to the blockchain.")
            return None

        # 2. Load the smart contract
        contract_address = Web3.to_checksum_address(settings.VOTING_CONTRACT_ADDRESS)
        contract_abi = settings.VOTING_CONTRACT_ABI
        contract = w3.eth.contract(address=contract_address, abi=contract_abi)

        # 3. Prepare the account that will send the transaction (Election Officer)
        officer_account = w3.eth.account.from_key(settings.ELECTION_OFFICER_PRIVATE_KEY)
        w3.eth.default_account = officer_account.address

        # 4. Prepare the data for the transaction
        # The smart contract expects a bytes32 hash of the student ID
        student_id_hash = Web3.keccak(text=student_id)

        # 5. Build the transaction to call the 'recordVote' function
        nonce = w3.eth.get_transaction_count(officer_account.address)
        
        tx_build = contract.functions.recordVote(student_id_hash).build_transaction({
            'from': officer_account.address,
            'nonce': nonce,
            # Let web3.py estimate gas. For testnets, this is usually fine.
            # You could also set manual gas prices if needed.
        })

        # 6. Sign the transaction with the officer's private key
        signed_tx = w3.eth.account.sign_transaction(tx_build, settings.ELECTION_OFFICER_PRIVATE_KEY)

        # 7. Send the signed transaction to the network
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        print(f"Blockchain transaction sent! Hash: {tx_hash.hex()}")

        # 8. (Optional but recommended) Wait for the transaction to be confirmed
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        
        print(f"Transaction confirmed! Block number: {tx_receipt.blockNumber}")
        
        return tx_hash.hex()

    except Exception as e:
        print(f"An error occurred while sending the blockchain transaction: {e}")
        # In a production app, you would add more robust error logging here
        # (e.g., save the failed transaction to a queue to retry later).
        return None